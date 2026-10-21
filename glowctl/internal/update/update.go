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
	"github.com/oxwrc/glow/glowctl/internal/odk"
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
	Compose func(releaseDir, envFile string) compose.Runner
	Docker  bundle.Runner
	Get     func(ctx context.Context, cfg *config.Config, host, path string) (int, error)
	// SyncForms publishes the new release's ODK forms; nil = the real sync.
	SyncForms func(ctx context.Context, releaseDir string) error
	Out       io.Writer
	Interval  time.Duration // health poll interval; 0 = 5s
	Attempts  int           // health polls; 0 = 60
}

// dbOf maps DB-backed services to the database a rollback restores.
var dbOf = map[string]string{"api": "glow", "api-db": "glow", "odk-service": "odk", "postgres14": "odk"}

// clientsOf are stopped while their database is restored.
var clientsOf = map[string]string{"glow": "api", "odk": "odk-service"}

// Run updates the deployment of o.ReleaseDir to o.Version.
func Run(ctx context.Context, o Options) (err error) {
	// Run from <parent>/current or the real dir alike: work on the real one.
	o.ReleaseDir = compose.Real(o.ReleaseDir)
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
	// A rollback restarts o.ReleaseDir: it must be the release actually running.
	if err := runningHere(st, o.ReleaseDir); err != nil {
		return err
	}

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
	// A release may add state dirs: create them before Docker would, root-owned.
	if err := install.EnsureStateDirs(cfg); err != nil {
		return err
	}
	if err := state.WriteRuntimeEnv(cfg, sec, newDir, nextEnv); err != nil {
		return err
	}
	mk := o.Compose
	if mk == nil {
		mk = func(dir, env string) compose.Runner {
			return compose.Exec{C: compose.WithEnvFile(dir, env, cfg.Project(), cfg.Demo)}
		}
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
	// The running stack is compared through <parent>/current when that is
	// where it runs from; the new release through its own dir.
	curPath := compose.CurrentPath(o.ReleaseDir)
	hadCurrent := compose.ProjectDir(o.ReleaseDir) == curPath
	oldSvcs, err := services(ctx, oldC, o.ReleaseDir, curPath)
	if err != nil {
		return err
	}
	newSvcs, err := services(ctx, nextC, newDir, curPath)
	if err != nil {
		return err
	}
	oldImgs, newImgs := imagesOf(oldSvcs), imagesOf(newSvcs)
	if err := suppressionGuard(ctx, docker, oldImgs["api"], newImgs["api"], cfg.Demo, step); err != nil {
		return err
	}
	// changed: images (a rollback restores their DBs); recreate: everything
	// whose container must be replaced (images, config, release files).
	changed := diff(oldImgs, newImgs)
	recreate, err := recreateSet(oldSvcs, newSvcs, o.ReleaseDir, newDir, hadCurrent)
	if err != nil {
		return err
	}
	if !hadCurrent {
		step("note: compose now runs from %s; services with release files are recreated once to move onto it", curPath)
	}
	if len(recreate) == 0 {
		step("changed services: none (re-applying %s)", v)
	} else {
		step("changed services: %s (%s)", strings.Join(recreate, ", "), impact(recreate))
	}
	if err := confirm(o, running, recreate); err != nil {
		return err
	}

	// 3. Backup, as late as possible.
	snap := ""
	if cfg.Demo {
		step("demo mode: no backup (a failed update restores images only)")
	} else {
		step("taking a backup")
		if snap, err = backup.NowSnapshot(ctx, oldC, o.Out); err != nil {
			return fmt.Errorf("pre-update backup failed (no snapshot id); nothing was changed: %w", err)
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
		cause = compose.SwitchCurrent(newDir)
	}
	if cause == nil {
		step("recreating changed containers")
		cause = up(ctx, newC, recreate)
	}
	if cause == nil {
		cause = ctx.Err() // interrupted (or the SSH session hung up): roll back
	}
	if cause == nil {
		step("checking health")
		cause = health(ctx, o, "the new release")
	}
	if cause != nil {
		// 7. Rollback. Ctrl-C must not interrupt it half way.
		return rollback(context.WithoutCancel(ctx), o, rollbackPlan{
			cause: cause, running: running, newDir: newDir, rtEnv: rtEnv, oldEnv: oldEnvB,
			oldC: oldC, changed: changed, recreate: existing(recreate, oldSvcs), snap: snap, step: step,
		})
	}

	if err := state.WriteDeployment(st, state.Deployment{Version: v, DeployedAt: time.Now(), Demo: cfg.Demo, ReleaseDir: newDir}); err != nil {
		return err
	}
	step("updated %s -> %s", running, v)
	// Published forms cannot be undone by a rollback, so they go last and a
	// failure only warns: the stack is healthy on the new release.
	step("syncing ODK forms")
	sync := o.SyncForms
	if sync == nil {
		sync = func(ctx context.Context, dir string) error { return syncForms(ctx, cfg, sec, dir, o.Out, step) }
	}
	if err := sync(ctx, newDir); err != nil {
		step("WARNING: publishing the new release's ODK forms failed: %v\n"+
			"[glowctl] the update itself succeeded; rerun `glowctl install --admin-email <an existing GLOW admin>` from %s to publish them", err, newDir)
	}
	step("new release dir: %s; %s now points to it (run glowctl from there from now on)", newDir, curPath)
	step("previous release dir, kept for rollback: %s", o.ReleaseDir)
	return nil
}

// syncForms publishes releaseDir's odk-forms like install does.
func syncForms(ctx context.Context, cfg *config.Config, sec state.Secrets, releaseDir string, out io.Writer, step func(string, ...any)) error {
	kv, err := state.RuntimeEnv(cfg, sec, releaseDir)
	if err != nil {
		return err
	}
	admin := ""
	for _, p := range kv {
		if p[0] == "ODK_SYSADMIN_EMAIL" {
			admin = p[1]
		}
	}
	c := odk.NewClient(cfg)
	tok, err := c.Login(ctx, admin, sec.ODKAdminPassword)
	if err != nil {
		return err
	}
	defer c.Logout(context.WithoutCancel(ctx), tok) //nolint:errcheck // best effort
	uploaded, err := odk.SyncForms(ctx, c, tok, odk.RequiredProjectID, filepath.Join(releaseDir, "odk-forms"),
		filepath.Join(cfg.StateDir, "odk-forms-state.json"), out)
	if err != nil {
		return err
	}
	if len(uploaded) == 0 {
		step("forms unchanged")
	} else {
		step("published forms: %s", strings.Join(uploaded, ", "))
	}
	return nil
}

// runningHere refuses a release dir other than the running one: deployment.json
// records the running dir (and version); a rollback restarts releaseDir.
func runningHere(stateDir, releaseDir string) error {
	d, err := state.ReadDeployment(stateDir)
	if err != nil {
		if os.IsNotExist(err) {
			return nil // never recorded: RunningVersion fell back to this dir's VERSION
		}
		return err
	}
	same := func(a, b string) bool {
		if ra, err := filepath.EvalSymlinks(a); err == nil {
			a = ra
		}
		if rb, err := filepath.EvalSymlinks(b); err == nil {
			b = rb
		}
		return filepath.Clean(a) == filepath.Clean(b)
	}
	here := state.ReleaseVersion(releaseDir)
	if d.ReleaseDir != "" && !same(d.ReleaseDir, releaseDir) {
		return fmt.Errorf("%s is not the running release (%s runs from %s); run that release's glowctl", releaseDir, d.Version, d.ReleaseDir)
	}
	if d.Version == "" || here == d.Version {
		return nil
	}
	var cands []string
	if es, err := os.ReadDir(filepath.Dir(releaseDir)); err == nil {
		for _, e := range es {
			p := filepath.Join(filepath.Dir(releaseDir), e.Name())
			if e.IsDir() && !same(p, releaseDir) && state.ReleaseVersion(p) == d.Version {
				cands = append(cands, p)
			}
		}
	}
	where := "find the release dir holding VERSION " + d.Version
	if len(cands) > 0 {
		where = "it is in " + strings.Join(cands, " or ")
	}
	return fmt.Errorf("%s holds %s but %s is running (%s); run that release's glowctl", releaseDir, here, d.Version, where)
}

type rollbackPlan struct {
	cause         error
	running       string
	newDir, rtEnv string
	oldEnv        []byte
	oldC          compose.Runner
	changed       []string // image changed
	recreate      []string // recreated by the update and present in the previous release
	snap          string
	step          func(string, ...any)
}

// manual is the by-hand recovery for a failed rollback: exact commands.
func manual(o Options, p rollbackPlan, names, clients []string) string {
	dc := func(args ...string) string {
		return "  " + strings.Join(compose.WithEnvFile(o.ReleaseDir, p.rtEnv, o.Cfg.Project(), o.Cfg.Demo).Argv(args...), " ")
	}
	var b strings.Builder
	fmt.Fprintf(&b, "Previous release dir: %s; new release dir (kept): %s\n", o.ReleaseDir, p.newDir)
	fmt.Fprintf(&b, "Recover by hand: make sure %s is the previous release's (it was rewritten for the rollback; "+
		"if not, rerun `glowctl install` from %s) and that %s is a symlink to %s, then run:\n",
		p.rtEnv, o.ReleaseDir, compose.CurrentPath(o.ReleaseDir), filepath.Base(o.ReleaseDir))
	if len(p.recreate) > 0 {
		b.WriteString(dc(append([]string{"up", "-d", "--no-deps", "--force-recreate"}, p.recreate...)...) + "\n")
	}
	b.WriteString(dc("up", "-d", "--remove-orphans") + "\n")
	if len(names) > 0 && p.snap != "" {
		b.WriteString(dc(append([]string{"stop"}, clients...)...) + "\n")
		b.WriteString(dc(append([]string{"exec", "-T", "backup", "glow-backup.sh", "rollback-dbs", p.snap}, names...)...) + "\n")
		b.WriteString(dc("up", "-d") + "\n")
	}
	fmt.Fprintf(&b, "  %s status\n", filepath.Join(o.ReleaseDir, "glowctl"))
	switch {
	case len(names) > 0 && p.snap != "":
		fmt.Fprintf(&b, "The pre-update backup is snapshot %s. Backup images of releases that lack rollback-dbs "+
			"(older than this glowctl) cannot run that step: instead run `sudo %s restore --from %s --snapshot %s --force` "+
			"from %s (restores both databases and the state files)",
			p.snap, filepath.Join(o.ReleaseDir, "glowctl"), backupPath(o.Cfg), p.snap, o.ReleaseDir)
	case p.snap != "":
		fmt.Fprintf(&b, "No database needs restoring; the pre-update backup is snapshot %s", p.snap)
	default:
		b.WriteString("There is no pre-update backup (demo mode)")
	}
	return b.String()
}

// backupPath is the restic repository on this host (compose's default).
func backupPath(cfg *config.Config) string {
	if cfg.BackupPath != "" {
		return cfg.BackupPath
	}
	return filepath.Join(cfg.StateDir, "backups")
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
		err = compose.SwitchCurrent(o.ReleaseDir)
	}
	if err == nil {
		p.step("starting the previous release")
	}
	if err == nil && len(p.recreate) > 0 {
		err = dc(append([]string{"up", "-d", "--no-deps", "--force-recreate"}, p.recreate...)...)
	}
	if err == nil {
		err = dc("up", "-d", "--remove-orphans")
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
		return fmt.Errorf("update to %s failed: %w\nthe rollback to %s ALSO FAILED: %v\n%s",
			o.Version, p.cause, p.running, err, manual(o, p, names, clients))
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

// freeDir is <parent>/glow-<version> (the archive's own top dir), or
// <parent>/glow-<version>.N if that exists
// (re-applying the running version, or a dir left by an earlier attempt).
func freeDir(parent, v string) string {
	p := filepath.Join(parent, "glow-"+v)
	for i := 2; ; i++ {
		if _, err := os.Lstat(p); os.IsNotExist(err) {
			return p
		}
		p = filepath.Join(parent, fmt.Sprintf("glow-%s.%d", v, i))
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
	src := filepath.Join(o.ReleaseDir, "glow.env")
	env, err := os.ReadFile(src)
	if err != nil {
		return err
	}
	fi, err := os.Stat(src)
	if err != nil {
		return err
	}
	// Same bytes and mode: the backup service mounts it (through current),
	// so a difference would recreate that container.
	dst := filepath.Join(rel, "glow.env")
	if err := os.WriteFile(dst, env, 0o600); err != nil {
		return err
	}
	if err := os.Chmod(dst, fi.Mode().Perm()); err != nil {
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

// services parses r's `compose config`; dirs are the release dir's paths.
func services(ctx context.Context, r compose.Runner, dirs ...string) (map[string]service, error) {
	out, err := r.Run(ctx, []string{"config", "--format", "json"}, nil)
	if err != nil {
		return nil, err
	}
	return parseServices(out, dirs...)
}

// existing keeps the names that are services of svcs.
func existing(names []string, svcs map[string]service) []string {
	var out []string
	for _, n := range names {
		if _, ok := svcs[n]; ok {
			out = append(out, n)
		}
	}
	return out
}

// imagesOf maps each service to its image ref.
func imagesOf(svcs map[string]service) map[string]string {
	m := map[string]string{}
	for n, s := range svcs {
		m[n] = s.Image
	}
	return m
}

// up recreates exactly the services in recreate (--no-deps: their running
// dependencies are left alone), then brings the rest up (removing services
// the release dropped), which leaves
// containers whose config is unchanged running: with the stable project
// dir, ODK and the proxy keep running through a GLOW-only update.
func up(ctx context.Context, r compose.Runner, recreate []string) error {
	if len(recreate) > 0 {
		if _, err := r.Run(ctx, append([]string{"up", "-d", "--no-deps", "--force-recreate"}, recreate...), nil); err != nil {
			return err
		}
	}
	// Services dropped from the new release would otherwise keep running,
	// their release bind paths dangling through current.
	_, err := r.Run(ctx, []string{"up", "-d", "--remove-orphans"}, nil)
	return err
}

// glowServices are GLOW's own; recreating any other service (ODK Central,
// its databases and helpers, the proxy) interrupts collection briefly.
var glowServices = map[string]bool{"api": true, "api-db": true, "backup": true, "dashboard": true, "glow-init": true}

func impact(recreate []string) string {
	for _, s := range recreate {
		if !glowServices[s] {
			return "ODK Central or the proxy restarts: collection is interrupted briefly"
		}
	}
	return "ODK Central and the proxy keep running"
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
	what := "no service changes"
	if len(changed) > 0 {
		what = strings.Join(changed, ", ") + " will be recreated (" + impact(changed) + ")"
	}
	fmt.Fprintf(o.Out, "Update %s -> %s: %s. Continue? [y/N] ", running, o.Version, what)
	line, _ := bufio.NewReader(o.Stdin).ReadString('\n')
	if a := strings.ToLower(strings.TrimSpace(line)); a != "y" && a != "yes" {
		return errors.New("cancelled; nothing was changed")
	}
	return nil
}
