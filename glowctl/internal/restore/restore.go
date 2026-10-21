// Package restore rebuilds a GLOW deployment from a restic backup.
//
// restic runs in the backup image via plain `docker run` (repo mounted
// read-only, no network, key on stdin): that works before install, when there
// is no runtime.env for compose yet, and never touches the state dir. The
// database restore needs the DB networks and the restored passwords, so it is
// a `compose run` of the backup service with the staging dir mounted.
package restore

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/oxwrc/glow/glowctl/internal/bundle"
	"github.com/oxwrc/glow/glowctl/internal/compose"
	"github.com/oxwrc/glow/glowctl/internal/config"
	"github.com/oxwrc/glow/glowctl/internal/install"
	"github.com/oxwrc/glow/glowctl/internal/lock"
	"github.com/oxwrc/glow/glowctl/internal/proxy"
	"github.com/oxwrc/glow/glowctl/internal/state"
)

// Options configures Run. Nil Docker/Get use the real implementations.
type Options struct {
	ReleaseDir string
	Cfg        *config.Config // the live glow.env of ReleaseDir
	From       string         // restic repository path on this host
	Snapshot   string         // snapshot ID or "latest"
	Key        string         // backup key; only ever sent on stdin
	Force      bool           // replace live data (originals kept as *.pre-restore)
	Compose    compose.Runner
	Docker     bundle.Runner
	Get        func(ctx context.Context, cfg *config.Config, host, path string) (int, error)
	Out        io.Writer
	Interval   time.Duration // health poll interval; 0 = 5s
}

// stateItems are swapped in from the snapshot; secrets.env is required.
var stateItems = []string{"secrets.env", "odk-secrets", "api-audit-logs", "vintages", "odk-forms-state.json"}

// dbDirs are the database data dirs; non-empty means live data.
var dbDirs = []string{"glow-postgres", "odk-postgres"}

type snapshot struct {
	ID   string `json:"id"`
	Time string `json:"time"`
}

// BackupImage is the backup image of this release, as compose names it.
func BackupImage(cfg *config.Config, releaseDir string) string {
	reg, v := cfg.Registry, cfg.Version
	if reg == "" {
		reg = "ghcr.io/oxwrc"
	}
	if v == "" {
		v = state.ReleaseVersion(releaseDir)
	}
	return reg + "/glow-backup:" + v
}

// Run restores the snapshot over the deployment of o.ReleaseDir.
func Run(ctx context.Context, o Options) (err error) {
	cfg, st := o.Cfg, o.Cfg.StateDir
	if cfg.Demo {
		return errors.New("demo mode has no backups to restore")
	}
	step := func(f string, a ...any) { fmt.Fprintf(o.Out, "[glowctl] "+f+"\n", a...) }
	d := o.Docker
	if d == nil {
		d = bundle.Docker{}
	}
	img := BackupImage(cfg, o.ReleaseDir)
	restic := func(mounts []string, args ...string) (string, error) {
		a := append([]string{"run", "--rm", "-i", "--network", "none", "-v", o.From + ":/repo:ro"}, mounts...)
		a = append(append(a, "-e", "RESTIC_REPOSITORY=/repo", img, "glow-backup.sh"), args...)
		return d.Run(ctx, a, strings.NewReader(o.Key+"\n"))
	}
	dc := func(args ...string) error { _, err := o.Compose.Run(ctx, args, nil); return err }

	// 1. Key first: a wrong key fails here, before anything is touched.
	step("checking the backup key against %s", o.From)
	out, err := restic(nil, "check-key", o.Snapshot)
	if err != nil {
		return fmt.Errorf("cannot open the backup with this backup key (wrong key, or not a GLOW backup); nothing was changed: %w", err)
	}
	var snap snapshot
	lines := strings.Split(strings.TrimSpace(out), "\n")
	if json.Unmarshal([]byte(lines[len(lines)-1]), &snap) != nil || snap.ID == "" {
		return fmt.Errorf("unexpected check-key output: %q", out)
	}
	step("snapshot %s taken %s", snap.ID, snap.Time)

	release, err := lock.Acquire(st)
	if err != nil {
		return err
	}
	defer release()

	// 2. Live data: running containers or non-empty DB dirs.
	ps, err := d.Run(ctx, []string{"ps", "-q", "--filter", "label=com.docker.compose.project=glow"}, nil)
	if err != nil {
		return err
	}
	var live []string
	if strings.TrimSpace(ps) != "" {
		live = append(live, "containers are running")
	}
	var fullDBs []string
	for _, n := range dbDirs {
		if es, _ := os.ReadDir(filepath.Join(st, n)); len(es) > 0 {
			fullDBs = append(fullDBs, n)
			live = append(live, st+"/"+n+" holds data")
		}
	}
	if len(live) > 0 {
		if !o.Force {
			return fmt.Errorf("this server has live data (%s); rerun with --force to replace it", strings.Join(live, ", "))
		}
		step("WARNING: current data will be replaced (%s); the originals are kept as *.pre-restore", strings.Join(live, ", "))
	}
	_, statErr := os.Stat(filepath.Join(st, "runtime.env"))
	if statErr == nil {
		step("stopping the stack")
		if err := dc("down"); err != nil {
			return err
		}
	} else if strings.TrimSpace(ps) != "" {
		return errors.New("glow containers are running but runtime.env is missing; stop them first")
	}

	// 3. Restore the snapshot's files into a staging dir on the state fs.
	for _, n := range append(append([]string{}, install.StateDirs...), "") {
		if err := os.MkdirAll(filepath.Join(st, n), 0o755); err != nil {
			return err
		}
	}
	staging, err := os.MkdirTemp(st, ".restore-staging-")
	if err != nil {
		return err
	}
	defer func() {
		// Holds database dumps (personal data): never leave it behind.
		if rerr := os.RemoveAll(staging); rerr != nil {
			err = errors.Join(err, fmt.Errorf("remove %s (it holds database dumps; delete it): %w", staging, rerr))
		}
	}()
	step("restoring files from the snapshot")
	if _, err := restic([]string{"-v", staging + ":/staging"}, "restore-files", snap.ID, "/staging"); err != nil {
		return err
	}
	src := filepath.Join(staging, "state")
	if _, err := os.Stat(filepath.Join(src, "secrets.env")); err != nil {
		return fmt.Errorf("snapshot has no secrets.env: %w", err)
	}
	for _, n := range []string{"glow.dump", "odk.dump"} {
		if _, err := os.Stat(filepath.Join(staging, "tmp", "glow-dumps", n)); err != nil {
			return fmt.Errorf("snapshot has no %s: %w", n, err)
		}
	}

	// 4. Swap the state items in; set the old DB dirs aside so the DBs start
	// fresh with the restored passwords.
	for _, n := range stateItems {
		p := filepath.Join(src, n)
		if _, err := os.Lstat(p); os.IsNotExist(err) {
			step("snapshot has no %s; keeping the current one", n)
			continue
		}
		if err := swap(p, filepath.Join(st, n)); err != nil {
			return err
		}
	}
	for _, n := range fullDBs {
		if err := setAside(filepath.Join(st, n)); err != nil {
			return err
		}
		if err := os.Mkdir(filepath.Join(st, n), 0o755); err != nil {
			return err
		}
	}
	if b, err := os.ReadFile(filepath.Join(staging, "release", "glow.env")); err == nil {
		if err := state.WriteFileAtomic(filepath.Join(o.ReleaseDir, "glow.env.restored"), b, 0o600); err != nil {
			return err
		}
	}

	// 5. The restored secrets are authoritative: the dumps' DB passwords.
	sec, _, err := state.EnsureSecrets(st)
	if err != nil {
		return err
	}
	if err := state.WriteRuntimeEnv(cfg, sec, o.ReleaseDir, filepath.Join(st, "runtime.env")); err != nil {
		return err
	}

	// 6. Databases.
	step("starting the databases")
	if err := dc("up", "-d", "--wait", "--pull", "missing", "api-db", "postgres14"); err != nil {
		return err
	}
	step("restoring the databases")
	if err := dc("run", "--rm", "--no-deps", "-T", "-v", filepath.Join(staging, "tmp", "glow-dumps")+":/restore:ro",
		"backup", "glow-backup.sh", "restore-dbs", "/restore"); err != nil {
		return err
	}

	// 7. Whole stack, health through the proxy.
	step("starting the stack")
	if err := dc("up", "-d", "--pull", "missing"); err != nil {
		return err
	}
	get := o.Get
	if get == nil {
		get = proxy.Get
	}
	iv := o.Interval
	if iv <= 0 {
		iv = 5 * time.Second
	}
	step("verifying through the proxy")
	if err := install.Verify(ctx, func(ctx context.Context, h, p string) (int, error) { return get(ctx, cfg, h, p) },
		proxy.Targets(cfg), 60, iv, o.Out, 30*time.Second, "the stack through the proxy"); err != nil {
		return err
	}
	if err := state.WriteDeployment(st, state.Deployment{Version: state.ReleaseVersion(o.ReleaseDir), DeployedAt: time.Now()}); err != nil {
		return err
	}
	step("restored snapshot %s (taken %s)", snap.ID, snap.Time)
	step("the snapshot's glow.env is in %s: compare it with glow.env and merge by hand", filepath.Join(o.ReleaseDir, "glow.env.restored"))
	step("replaced files and dirs are kept as *.pre-restore in %s; delete them once satisfied", st)
	return nil
}

// swap moves the staged src into dst's place, keeping any existing dst as
// dst.pre-restore. Both are on the state fs, so each step is a rename.
func swap(src, dst string) error {
	if err := setAside(dst); err != nil {
		return err
	}
	if err := os.Rename(src, dst); err != nil {
		return err
	}
	return syncDir(filepath.Dir(dst))
}

// setAside renames p to p.pre-restore (or .pre-restore.N if taken); missing p is fine.
func setAside(p string) error {
	if _, err := os.Lstat(p); os.IsNotExist(err) {
		return nil
	}
	to := p + ".pre-restore"
	for i := 2; ; i++ {
		if _, err := os.Lstat(to); os.IsNotExist(err) {
			break
		}
		to = p + ".pre-restore." + strconv.Itoa(i)
	}
	return os.Rename(p, to)
}

func syncDir(dir string) error {
	f, err := os.Open(dir)
	if err != nil {
		return err
	}
	defer f.Close()
	if err := f.Sync(); err != nil && !isUnsupported(err) {
		return err
	}
	return nil
}

// isUnsupported: directory fsync is unsupported on some filesystems (NFS, FUSE).
func isUnsupported(err error) bool {
	return errors.Is(err, syscall.EINVAL) || errors.Is(err, syscall.ENOTSUP) || errors.Is(err, syscall.ENOSYS)
}
