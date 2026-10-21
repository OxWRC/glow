// Package update moves a deployment to a new release, rolling back on failure.
//
// Release dirs sit side by side under a common parent; the new one is
// unpacked next to the running one, which is kept (never deleted) so the
// operator can roll back. Until the swap the new release runs compose against
// <state>/runtime.env.next, so the running stack's runtime.env is untouched
// by every step that can be aborted with zero downtime.
package update

import (
	"archive/tar"
	"bufio"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/oxwrc/glow/glowctl/internal/backup"
	"github.com/oxwrc/glow/glowctl/internal/bundle"
	"github.com/oxwrc/glow/glowctl/internal/compose"
	"github.com/oxwrc/glow/glowctl/internal/config"
	"github.com/oxwrc/glow/glowctl/internal/install"
	"github.com/oxwrc/glow/glowctl/internal/lock"
	"github.com/oxwrc/glow/glowctl/internal/proxy"
	"github.com/oxwrc/glow/glowctl/internal/state"
)

// ReleaseURL is where release assets live: <ReleaseURL>/<tag>/<file>.
const ReleaseURL = "https://github.com/OxWRC/glow/releases/download"

// Options configures Run. Nil Compose/Docker/Get/HTTP use the real implementations.
type Options struct {
	ReleaseDir string         // the running release
	Cfg        *config.Config // its glow.env
	Version    string         // target tag vX.Y.Z
	Bundle     string         // offline: glow-<tag>.tar.gz in a bundle dir (or the dir)
	Yes        bool           // no confirmation prompt
	Stdin      io.Reader
	IsTTY      bool
	BaseURL    string // default ReleaseURL
	HTTP       *http.Client
	// Compose runs compose for a release dir with the given env file.
	Compose  func(releaseDir, envFile string) compose.Runner
	Docker   bundle.Runner
	Get      func(ctx context.Context, cfg *config.Config, host, path string) (int, error)
	Out      io.Writer
	Interval time.Duration // health poll interval; 0 = 5s
	Attempts int           // health polls; 0 = 60
}

// dbOf maps DB-backed services to the database a rollback restores.
var dbOf = map[string]string{"api": "glow", "api-db": "glow", "odk-service": "odk", "postgres14": "odk"}

// clientsOf are stopped while their database is restored.
var clientsOf = map[string]string{"glow": "api", "odk": "odk-service"}

// Run updates the deployment of o.ReleaseDir to o.Version.
func Run(ctx context.Context, o Options) (err error) {
	cfg, st, v := o.Cfg, o.Cfg.StateDir, o.Version
	step := func(f string, a ...any) { fmt.Fprintf(o.Out, "[glowctl] "+f+"\n", a...) }
	if !tagRe.MatchString(v) {
		return fmt.Errorf("version %q is not a release tag like v1.2.3", v)
	}
	if cfg.Version != "" && cfg.Version != v {
		return fmt.Errorf("glow.env pins GLOW_VERSION=%s; remove that line to update", cfg.Version)
	}
	// Downgrades are refused before anything is fetched: the tag is all it takes.
	running := state.RunningVersion(st, o.ReleaseDir)
	switch c, ok := Compare(v, running); {
	case !ok && !cfg.Demo:
		return fmt.Errorf("the running version %q is unknown, so a downgrade cannot be ruled out; nothing was changed", running)
	case !ok:
		step("note: the running version %q is unknown (demo: continuing)", running)
	case c < 0:
		return fmt.Errorf("refusing to downgrade from %s to %s; nothing was changed", running, v)
	}

	release, err := lock.Acquire(st)
	if err != nil {
		return err
	}
	defer release()

	newDir := freeDir(filepath.Dir(o.ReleaseDir), v)
	swapped := false
	defer func() {
		if err != nil && !swapped {
			os.RemoveAll(newDir) //nolint:errcheck // nothing ran from it
		}
	}()
	docker := o.Docker
	if docker == nil {
		docker = bundle.Docker{}
	}
	if err := fetch(ctx, o, newDir, step); err != nil {
		return err
	}
	if o.Bundle != "" {
		if err := bundle.Load(ctx, docker, bundleDir(o.Bundle), newDir, o.Out); err != nil {
			return err
		}
	}

	rtEnv, nextEnv := filepath.Join(st, "runtime.env"), filepath.Join(st, "runtime.env.next")
	defer os.Remove(nextEnv) // holds secrets
	sec, _, err := state.EnsureSecrets(st)
	if err != nil {
		return err
	}
	if err := state.WriteRuntimeEnv(cfg, sec, newDir, nextEnv); err != nil {
		return err
	}
	mk := o.Compose
	if mk == nil {
		mk = func(dir, env string) compose.Runner { return compose.Exec{C: compose.WithEnvFile(dir, env, cfg.Demo)} }
	}
	oldC, nextC, newC := mk(o.ReleaseDir, rtEnv), mk(newDir, nextEnv), mk(newDir, rtEnv)
	dc := func(r compose.Runner, args ...string) (string, error) { return r.Run(ctx, args, nil) }

	// 1. Images.
	if o.Bundle == "" {
		step("pulling images")
		if _, err := dc(nextC, "pull"); err != nil {
			return fmt.Errorf("pulling the new images failed; nothing was changed: %w", err)
		}
	}
	// 2. Config against the new API image, then the suppression-rules guard.
	step("checking glow.env against the new release")
	if out, err := dc(nextC, "run", "--rm", "--no-deps", "api", "glow-api", "config", "check"); err != nil {
		fmt.Fprint(o.Out, out)
		return fmt.Errorf("the new release rejects this configuration; nothing was changed: %w", err)
	}
	oldImgs, err := images(ctx, oldC)
	if err != nil {
		return err
	}
	newImgs, err := images(ctx, nextC)
	if err != nil {
		return err
	}
	if err := suppressionGuard(ctx, docker, oldImgs["api"], newImgs["api"], cfg.Demo, step); err != nil {
		return err
	}
	changed := diff(oldImgs, newImgs)
	if len(changed) == 0 {
		step("changed services: none (re-applying %s)", v)
	} else {
		step("changed services: %s", strings.Join(changed, ", "))
	}
	if err := confirm(o, running, changed); err != nil {
		return err
	}

	// 3. Backup, as late as possible.
	snap := ""
	if cfg.Demo {
		step("demo mode: no backup (a failed update restores images only)")
	} else {
		step("taking a backup")
		if err := backup.Now(ctx, oldC, o.Out); err != nil {
			return fmt.Errorf("backup failed; nothing was changed: %w", err)
		}
		if snap, err = latestSnapshot(ctx, oldC); err != nil {
			return fmt.Errorf("cannot identify the pre-update backup; nothing was changed: %w", err)
		}
		step("pre-update backup: snapshot %s", snap)
	}

	// 4–6. Swap: runtime.env for the new release, recreate what changed
	// (migrations run on API start), health.
	oldEnvB, err := os.ReadFile(rtEnv)
	if err != nil {
		return err
	}
	newEnvB, err := os.ReadFile(nextEnv)
	if err != nil {
		return err
	}
	swapped = true
	cause := state.WriteFileAtomic(rtEnv, newEnvB, 0o600)
	if cause == nil {
		step("recreating changed containers")
		_, cause = dc(newC, "up", "-d")
	}
	if cause == nil {
		step("checking health")
		cause = health(ctx, o, "the new release")
	}
	if cause != nil {
		// 7. Rollback. Ctrl-C must not interrupt it half way.
		return rollback(context.WithoutCancel(ctx), o, rollbackPlan{
			cause: cause, running: running, newDir: newDir, rtEnv: rtEnv, oldEnv: oldEnvB,
			oldC: oldC, changed: changed, snap: snap, step: step,
		})
	}

	if err := state.WriteDeployment(st, state.Deployment{Version: v, DeployedAt: time.Now(), Demo: cfg.Demo}); err != nil {
		return err
	}
	step("updated %s -> %s", running, v)
	step("new release dir: %s (run glowctl from there from now on)", newDir)
	step("previous release dir, kept for rollback: %s", o.ReleaseDir)
	return nil
}

type rollbackPlan struct {
	cause         error
	running       string
	newDir, rtEnv string
	oldEnv        []byte
	oldC          compose.Runner
	changed       []string
	snap          string
	step          func(string, ...any)
}

// rollback brings the previous release back, restores the databases of
// changed DB-backed services from the pre-update snapshot, and checks health.
func rollback(ctx context.Context, o Options, p rollbackPlan) error {
	p.step("update failed: %v", p.cause)
	p.step("rolling back to %s", p.running)
	dc := func(args ...string) error { _, err := p.oldC.Run(ctx, args, nil); return err }
	dbs := map[string]bool{}
	for _, s := range p.changed {
		if db, ok := dbOf[s]; ok {
			dbs[db] = true
		}
	}
	var names, clients []string
	for _, db := range []string{"glow", "odk"} {
		if dbs[db] {
			names, clients = append(names, db), append(clients, clientsOf[db])
		}
	}
	restored := "none (no changed service uses a database)"
	err := state.WriteFileAtomic(p.rtEnv, p.oldEnv, 0o600)
	if err == nil {
		p.step("starting the previous release")
		err = dc("up", "-d")
	}
	if err == nil && len(names) > 0 {
		if p.snap == "" {
			restored = "none (demo mode has no backups)"
		} else {
			p.step("restoring database(s) %s from snapshot %s", strings.Join(names, ", "), p.snap)
			err = dc(append([]string{"stop"}, clients...)...)
			if err == nil {
				err = dc(append([]string{"exec", "-T", "backup", "glow-backup.sh", "rollback-dbs", p.snap}, names...)...)
			}
			if err == nil {
				err = dc("up", "-d")
			}
			restored = strings.Join(names, ", ") + " from snapshot " + p.snap +
				" (changes made to them since the backup are lost)"
		}
	}
	if err == nil {
		p.step("checking health")
		err = health(ctx, o, "the previous release")
	}
	if err != nil {
		snapNote := "there is no pre-update backup (demo mode)"
		if p.snap != "" {
			snapNote = "the pre-update backup is snapshot " + p.snap
		}
		return fmt.Errorf("update to %s failed: %w\nthe rollback to %s ALSO FAILED: %v\n%s; previous release dir: %s; new release dir: %s",
			o.Version, p.cause, p.running, err, snapNote, o.ReleaseDir, p.newDir)
	}
	return fmt.Errorf("update to %s failed: %w\nrolled back to %s (%s); databases restored: %s\nthe new release dir %s is kept for inspection",
		o.Version, p.cause, p.running, o.ReleaseDir, restored, p.newDir)
}

func health(ctx context.Context, o Options, what string) error {
	get := o.Get
	if get == nil {
		get = proxy.Get
	}
	iv, n := o.Interval, o.Attempts
	if iv <= 0 {
		iv = 5 * time.Second
	}
	if n <= 0 {
		n = 60
	}
	return install.Verify(ctx, func(ctx context.Context, h, p string) (int, error) { return get(ctx, o.Cfg, h, p) },
		proxy.Targets(o.Cfg), n, iv, o.Out, 30*time.Second, what)
}

// freeDir is <parent>/<version>, or <parent>/<version>.N if that exists
// (re-applying the running version, or a dir left by an earlier attempt).
func freeDir(parent, v string) string {
	p := filepath.Join(parent, v)
	for i := 2; ; i++ {
		if _, err := os.Lstat(p); os.IsNotExist(err) {
			return p
		}
		p = filepath.Join(parent, fmt.Sprintf("%s.%d", v, i))
	}
}

func bundleDir(b string) string {
	if fi, err := os.Stat(b); err == nil && !fi.IsDir() {
		return filepath.Dir(b)
	}
	return b
}

// fetch downloads (or takes from the bundle) the release archive, verifies
// it against SHA256SUMS, unpacks it and copies glow.env in, then moves it to
// newDir, so newDir only ever holds a complete release.
func fetch(ctx context.Context, o Options, newDir string, step func(string, ...any)) error {
	staging, err := os.MkdirTemp(filepath.Dir(newDir), ".glow-update-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(staging)
	name := "glow-" + o.Version + ".tar.gz"
	var archive, sums string
	if o.Bundle != "" {
		dir := bundleDir(o.Bundle)
		archive, sums = filepath.Join(dir, name), filepath.Join(dir, "SHA256SUMS")
		if dir != o.Bundle {
			archive = o.Bundle
		}
		step("using offline bundle %s", dir)
	} else {
		base := o.BaseURL
		if base == "" {
			base = ReleaseURL
		}
		c := o.HTTP
		if c == nil {
			c = &http.Client{Timeout: 15 * time.Minute}
		}
		archive, sums = filepath.Join(staging, name), filepath.Join(staging, "SHA256SUMS")
		for _, f := range []string{"SHA256SUMS", name} {
			step("downloading %s/%s/%s", base, o.Version, f)
			if err := download(ctx, c, base+"/"+o.Version+"/"+f, filepath.Join(staging, f)); err != nil {
				return err
			}
		}
	}
	want, err := sumFor(sums, filepath.Base(archive))
	if err != nil {
		return err
	}
	got, err := fileSHA256(archive)
	if err != nil {
		return err
	}
	if !strings.EqualFold(got, want) {
		return fmt.Errorf("%s: checksum mismatch (corrupt or tampered download); nothing was changed", filepath.Base(archive))
	}
	rel := filepath.Join(staging, "release")
	if err := os.Mkdir(rel, 0o755); err != nil {
		return err
	}
	f, err := os.Open(archive)
	if err != nil {
		return err
	}
	err = unpack(f, rel)
	f.Close()
	if err != nil {
		return fmt.Errorf("unpacking %s: %w", filepath.Base(archive), err)
	}
	if rv := state.ReleaseVersion(rel); rv != o.Version {
		return fmt.Errorf("%s holds release %q, not %s", filepath.Base(archive), rv, o.Version)
	}
	env, err := os.ReadFile(filepath.Join(o.ReleaseDir, "glow.env"))
	if err != nil {
		return err
	}
	if err := os.WriteFile(filepath.Join(rel, "glow.env"), env, 0o600); err != nil {
		return err
	}
	if err := os.Rename(rel, newDir); err != nil {
		return err
	}
	step("unpacked %s into %s", o.Version, newDir)
	return nil
}

func download(ctx context.Context, c *http.Client, url, dst string) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return err
	}
	resp, err := c.Do(req)
	if err != nil {
		return fmt.Errorf("downloading %s: %w", url, err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("downloading %s: HTTP %d", url, resp.StatusCode)
	}
	f, err := os.Create(dst)
	if err != nil {
		return err
	}
	if _, err := io.Copy(f, resp.Body); err != nil {
		f.Close()
		return fmt.Errorf("downloading %s: %w", url, err)
	}
	return f.Close()
}

// sumFor returns the SHA-256 that SHA256SUMS lists for name.
func sumFor(sums, name string) (string, error) {
	b, err := os.ReadFile(sums)
	if err != nil {
		return "", err
	}
	for _, line := range strings.Split(string(b), "\n") {
		sum, n, ok := strings.Cut(strings.TrimSpace(line), " ")
		if ok && strings.TrimPrefix(strings.TrimSpace(n), "*") == name && len(sum) == 64 {
			return sum, nil
		}
	}
	return "", fmt.Errorf("%s is not listed in SHA256SUMS; nothing was changed", name)
}

func fileSHA256(p string) (string, error) {
	f, err := os.Open(p)
	if err != nil {
		return "", err
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

// unpack extracts a release .tar.gz into dst, dropping the top-level
// glow-<tag>/ dir. Only plain files and dirs are accepted, and every path
// must stay inside dst.
func unpack(r io.Reader, dst string) error {
	gz, err := gzip.NewReader(r)
	if err != nil {
		return err
	}
	defer gz.Close()
	tr := tar.NewReader(gz)
	for {
		h, err := tr.Next()
		if err == io.EOF {
			return nil
		}
		if err != nil {
			return err
		}
		if path.IsAbs(h.Name) {
			return fmt.Errorf("entry %q: absolute path", h.Name)
		}
		top, rest, _ := strings.Cut(path.Clean(h.Name), "/")
		if top == ".." || (rest != "" && !filepath.IsLocal(rest)) {
			return fmt.Errorf("entry %q: path escapes the release dir", h.Name)
		}
		if rest == "" {
			if h.Typeflag == tar.TypeDir {
				continue // the top-level dir itself
			}
			return fmt.Errorf("entry %q: outside the release's top-level dir", h.Name)
		}
		p := filepath.Join(dst, filepath.FromSlash(rest))
		switch h.Typeflag {
		case tar.TypeDir:
			if err := os.MkdirAll(p, 0o755); err != nil {
				return err
			}
		case tar.TypeReg:
			if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
				return err
			}
			f, err := os.OpenFile(p, os.O_WRONLY|os.O_CREATE|os.O_EXCL, os.FileMode(h.Mode).Perm()&0o755)
			if err != nil {
				return err
			}
			_, err = io.Copy(f, tr)
			if cerr := f.Close(); err == nil {
				err = cerr
			}
			if err != nil {
				return err
			}
		default:
			return fmt.Errorf("entry %q: unsupported type %q (only files and dirs)", h.Name, h.Typeflag)
		}
	}
}

// images maps each service to its image ref in `compose config`.
func images(ctx context.Context, r compose.Runner) (map[string]string, error) {
	out, err := r.Run(ctx, []string{"config", "--format", "json"}, nil)
	if err != nil {
		return nil, err
	}
	var c struct {
		Services map[string]struct {
			Image string `json:"image"`
		} `json:"services"`
	}
	if err := json.Unmarshal([]byte(out), &c); err != nil {
		return nil, fmt.Errorf("parsing compose config: %w", err)
	}
	m := map[string]string{}
	for s, v := range c.Services {
		m[s] = v.Image
	}
	return m, nil
}

// diff lists services whose image ref differs (or that exist on one side only).
func diff(a, b map[string]string) []string {
	var out []string
	for s := range a {
		if a[s] != b[s] {
			out = append(out, s)
		}
	}
	for s := range b {
		if _, ok := a[s]; !ok {
			out = append(out, s)
		}
	}
	sort.Strings(out)
	return out
}

// suppressionGuard compares the suppression-rules hash of the running and new
// API images: the same dataset must never be releasable under two coarsenings.
func suppressionGuard(ctx context.Context, d bundle.Runner, oldImg, newImg string, demo bool, step func(string, ...any)) error {
	hash := func(img string) (string, error) {
		if img == "" {
			return "", errors.New("no api image")
		}
		out, err := d.Run(ctx, []string{"run", "--rm", "-e", "GLOW_DEMO_MODE=true", img, "glow-api", "suppression-hash"}, nil)
		if err != nil {
			return "", err
		}
		lines := strings.Fields(strings.TrimSpace(out))
		if len(lines) == 0 {
			return "", errors.New("empty suppression-hash output")
		}
		return lines[len(lines)-1], nil
	}
	var why string
	oldH, oerr := hash(oldImg)
	newH, nerr := hash(newImg)
	switch {
	case oerr != nil:
		why = fmt.Sprintf("cannot read the running release's suppression rules (%v)", oerr)
	case nerr != nil:
		why = fmt.Sprintf("cannot read the new release's suppression rules (%v)", nerr)
	case oldH != newH:
		why = "suppression rules differ between the running and the new release"
	default:
		return nil
	}
	if demo {
		step("note: %s; demo mode: continuing", why)
		return nil
	}
	return fmt.Errorf("%s. The same dataset must never be releasable under two suppression coarsenings: "+
		"a suppression rule change needs a fresh deployment. Nothing was changed", why)
}

func confirm(o Options, running string, changed []string) error {
	if o.Yes {
		return nil
	}
	if !o.IsTTY || o.Stdin == nil {
		return errors.New("not a terminal: pass --yes to update without a prompt; nothing was changed")
	}
	what := "no service images change"
	if len(changed) > 0 {
		what = strings.Join(changed, ", ") + " will be recreated (brief downtime)"
	}
	fmt.Fprintf(o.Out, "Update %s -> %s: %s. Continue? [y/N] ", running, o.Version, what)
	line, _ := bufio.NewReader(o.Stdin).ReadString('\n')
	if a := strings.ToLower(strings.TrimSpace(line)); a != "y" && a != "yes" {
		return errors.New("cancelled; nothing was changed")
	}
	return nil
}

// latestSnapshot is the newest GLOW snapshot, read right after the backup and
// before the swap, so a scheduled backup taken later can never be mistaken
// for the pre-update one.
func latestSnapshot(ctx context.Context, r compose.Runner) (string, error) {
	out, err := r.Run(ctx, []string{"exec", "-T", "backup", "restic", "snapshots", "--json", "--tag", "glow", "--host", "glow", "--latest", "1"}, nil)
	if err != nil {
		return "", err
	}
	var snaps []struct {
		ID   string    `json:"id"`
		Time time.Time `json:"time"`
	}
	if err := json.Unmarshal([]byte(out), &snaps); err != nil {
		return "", fmt.Errorf("parsing restic snapshots: %w", err)
	}
	id, at := "", time.Time{}
	for _, s := range snaps {
		if s.ID != "" && !s.Time.Before(at) {
			id, at = s.ID, s.Time
		}
	}
	if id == "" {
		return "", errors.New("no GLOW snapshot found")
	}
	return id, nil
}
