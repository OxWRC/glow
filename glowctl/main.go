// Command glowctl installs and operates the GLOW Docker Compose stack.
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"

	"github.com/oxwrc/glow/glowctl/internal/backup"
	"github.com/oxwrc/glow/glowctl/internal/compose"
	"github.com/oxwrc/glow/glowctl/internal/config"
	"github.com/oxwrc/glow/glowctl/internal/doctor"
	"github.com/oxwrc/glow/glowctl/internal/install"
	"github.com/oxwrc/glow/glowctl/internal/lock"
	"github.com/oxwrc/glow/glowctl/internal/restore"
	"github.com/oxwrc/glow/glowctl/internal/state"
	"github.com/oxwrc/glow/glowctl/internal/status"
	"github.com/oxwrc/glow/glowctl/internal/users"
)

// version is set at release time with -ldflags "-X main.version=<tag>".
var version = "dev"

const usage = "usage: glowctl <install|status|backup|restore|doctor|users|version> [--dir DIR] [args...]"

var commands = map[string]func(args []string) int{
	"install": installCmd,
	"status":  statusCmd,
	"backup":  backupCmd,
	"restore": restoreCmd,
	"doctor":  doctorCmd,
	"users":   usersCmd,
	"version": func([]string) int { fmt.Println("glowctl " + version); return 0 },
}

func fail(name string, err error) int {
	fmt.Fprintf(os.Stderr, "[glowctl] %s: %v\n", name, err)
	return 1
}

// splitDir extracts --dir/-dir from args (anywhere) and returns the rest, so
// subcommands with their own flags and positionals keep working.
func splitDir(args []string) (dir string, rest []string, err error) {
	dir = defaultDir()
	for i := 0; i < len(args); i++ {
		a := args[i]
		switch {
		case a == "--dir" || a == "-dir":
			if i+1 >= len(args) {
				return "", nil, fmt.Errorf("--dir needs a value")
			}
			dir = args[i+1]
			i++
		case strings.HasPrefix(a, "--dir="):
			dir = strings.TrimPrefix(a, "--dir=")
		default:
			rest = append(rest, a)
		}
	}
	dir, err = filepath.Abs(dir)
	return dir, rest, err
}

func loadValid(dir string) (*config.Config, error) {
	cfg, err := config.Load(filepath.Join(dir, "glow.env"))
	if err != nil {
		return nil, err
	}
	if errs := cfg.Validate(); len(errs) > 0 {
		return nil, fmt.Errorf("glow.env is invalid:\n%s", config.FormatErrors(errs))
	}
	return cfg, nil
}

// requireInstalled fails with a plain message when install has not run yet.
func requireInstalled(cfg *config.Config) error {
	if _, err := os.Stat(filepath.Join(cfg.StateDir, "runtime.env")); err != nil {
		return fmt.Errorf("not installed; run glowctl install")
	}
	return nil
}

func statusCmd(args []string) int {
	dir, rest, err := splitDir(args)
	if err != nil || len(rest) > 0 {
		return fail("status", errors.Join(err, fmt.Errorf("usage: glowctl status [--dir DIR]")))
	}
	cfg, err := loadValid(dir)
	if err == nil {
		err = requireInstalled(cfg)
	}
	if err != nil {
		return fail("status", err)
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	cs, err := status.Run(ctx, status.Options{
		Cfg: cfg, Version: state.RunningVersion(cfg.StateDir, dir),
		Runner: compose.Exec{C: compose.New(dir, cfg.StateDir, cfg.Demo)},
	})
	if err != nil {
		return fail("status", err)
	}
	for _, c := range cs {
		mark := "ok  "
		switch {
		case !c.OK:
			mark = "FAIL"
		case c.Warn:
			mark = "warn"
		}
		fmt.Printf("[glowctl] %s %-40s %s\n", mark, c.Name, c.Detail)
	}
	if !status.AllOK(cs) {
		return 1
	}
	return 0
}

func backupCmd(args []string) int {
	const u = "usage: glowctl backup [--dir DIR] [--verify]"
	dir, rest, err := splitDir(args)
	verify := false
	for _, a := range rest {
		if a != "--verify" && a != "-verify" {
			fmt.Fprintf(os.Stderr, "[glowctl] backup: unexpected argument %q\n%s\n", a, u)
			return 2
		}
		verify = true
	}
	if err != nil {
		return fail("backup", err)
	}
	cfg, err := loadValid(dir)
	if err == nil {
		err = requireInstalled(cfg)
	}
	if err == nil && cfg.Demo {
		err = errors.New("backups are disabled in demo mode")
	}
	if err != nil {
		return fail("backup", err)
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	r := compose.Exec{C: compose.New(dir, cfg.StateDir, cfg.Demo)}
	if err := runBackup(ctx, cfg.StateDir, r, verify, os.Stdout); err != nil {
		return fail("backup", err)
	}
	return 0
}

// runBackup holds the glowctl lock while the backup service runs one backup
// (then prune) or restore test; the outcome lands in backup-status.json.
func runBackup(ctx context.Context, stateDir string, r compose.Runner, verify bool, out io.Writer) error {
	release, err := lock.Acquire(stateDir)
	if err != nil {
		return err
	}
	defer release()
	if verify {
		return backup.Verify(ctx, r, out)
	}
	return backup.Now(ctx, r, out)
}

const restoreUsage = "usage: glowctl restore --from REPO [--snapshot ID|latest] [--key-file FILE] [--force] [--dir DIR]"

func restoreCmd(args []string) int {
	dir, rest, err := splitDir(args)
	if err != nil {
		return fail("restore", err)
	}
	fs := flag.NewFlagSet("restore", flag.ContinueOnError)
	from := fs.String("from", "", "restic repository path (a copy of the backup path)")
	snap := fs.String("snapshot", "latest", "snapshot ID")
	keyFile := fs.String("key-file", "", "file holding the backup key (else prompted on a TTY)")
	force := fs.Bool("force", false, "replace live data (originals kept as *.pre-restore)")
	if err := fs.Parse(rest); err != nil || fs.NArg() > 0 || *from == "" {
		fmt.Fprintln(os.Stderr, restoreUsage)
		return 2
	}
	// restic restores files with their original owners and the staging dir
	// holds root-owned dumps: only root can swap them in and clean up.
	if os.Geteuid() != 0 {
		return fail("restore", errors.New("must run as root (sudo): restored files keep their original owners"))
	}
	repo, err := filepath.Abs(*from)
	if err == nil {
		_, err = os.Stat(filepath.Join(repo, "config"))
	}
	if err != nil {
		return fail("restore", fmt.Errorf("--from is not a restic repository: %w", err))
	}
	cfg, err := loadValid(dir)
	if err != nil {
		return fail("restore", err)
	}
	key, err := readKey(*keyFile)
	if err != nil {
		return fail("restore", err)
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	err = restore.Run(ctx, restore.Options{
		ReleaseDir: dir, Cfg: cfg, From: repo, Snapshot: *snap, Key: key, Force: *force,
		Compose: compose.Exec{C: compose.New(dir, cfg.StateDir, cfg.Demo)}, Out: os.Stdout,
	})
	if err != nil {
		return fail("restore", err)
	}
	return 0
}

// readKey reads the backup key from file, or from the TTY without echo.
func readKey(file string) (string, error) {
	var b []byte
	var err error
	if file != "" {
		b, err = os.ReadFile(file)
	} else {
		if fi, serr := os.Stdin.Stat(); serr != nil || fi.Mode()&os.ModeCharDevice == 0 {
			return "", errors.New("no backup key: pass --key-file FILE or run on a terminal to be prompted")
		}
		stty := func(a string) error { c := exec.Command("stty", a); c.Stdin = os.Stdin; return c.Run() }
		if err := stty("-echo"); err != nil {
			return "", fmt.Errorf("cannot turn off terminal echo: %w", err)
		}
		fmt.Fprint(os.Stderr, "Backup key: ")
		var line string
		_, err = fmt.Fscanln(os.Stdin, &line)
		stty("echo") //nolint:errcheck
		fmt.Fprintln(os.Stderr)
		b = []byte(line)
	}
	if err != nil {
		return "", fmt.Errorf("reading backup key: %w", err)
	}
	k := strings.TrimSpace(string(b))
	if k == "" || strings.ContainsAny(k, "\n\r") {
		return "", errors.New("backup key must be one non-empty line")
	}
	return k, nil
}

func doctorCmd(args []string) int {
	dir, rest, err := splitDir(args)
	if err != nil || len(rest) > 0 {
		return fail("doctor", errors.Join(err, fmt.Errorf("usage: glowctl doctor [--dir DIR]")))
	}
	var errs []error
	cfg, err := config.Load(filepath.Join(dir, "glow.env"))
	if err != nil {
		errs = append(errs, err)
	} else {
		errs = cfg.Validate()
	}
	installed := false
	if cfg != nil {
		_, serr := os.Stat(filepath.Join(cfg.StateDir, "runtime.env"))
		installed = serr == nil
	}
	fs := doctor.Run(cfg, errs, doctor.HostEnv(installed))
	for _, f := range fs {
		fmt.Printf("[glowctl] %s: %s\n", f.Level, f.Msg)
	}
	if doctor.HasErrors(fs) {
		return 1
	}
	if len(fs) == 0 {
		fmt.Println("[glowctl] no problems found")
	}
	return 0
}

func usersCmd(args []string) int {
	dir, rest, err := splitDir(args)
	if err != nil {
		return fail("users", err)
	}
	cfg, err := loadValid(dir)
	if err == nil {
		err = requireInstalled(cfg)
	}
	if err != nil {
		return fail("users", err)
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	r := compose.Exec{C: compose.New(dir, cfg.StateDir, cfg.Demo)}
	if err := users.Run(ctx, r, rest, os.Stdout); err != nil {
		return fail("users", err)
	}
	return 0
}

// defaultDir is the binary's directory when it holds compose.yml, else the cwd.
func defaultDir() string {
	if exe, err := os.Executable(); err == nil {
		d := filepath.Dir(exe)
		if _, err := os.Stat(filepath.Join(d, "compose.yml")); err == nil {
			return d
		}
	}
	d, _ := os.Getwd()
	return d
}

func installCmd(args []string) int {
	fs := flag.NewFlagSet("install", flag.ContinueOnError)
	dir := fs.String("dir", defaultDir(), "release directory")
	admin := fs.String("admin-email", "", "first GLOW admin (production; prompted on a TTY)")
	bundleDir := fs.String("bundle", "", "offline bundle directory (images loaded instead of pulled)")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	if fs.NArg() > 0 {
		fmt.Fprintf(os.Stderr, "[glowctl] install: unexpected argument %q\n%s\n", fs.Arg(0), "usage: glowctl install [--dir DIR] [--admin-email EMAIL] [--bundle DIR]")
		return 2
	}
	rel, err := filepath.Abs(*dir)
	if err != nil {
		fmt.Fprintf(os.Stderr, "[glowctl] install: %v\n", err)
		return 1
	}
	fi, _ := os.Stdin.Stat()
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	err = install.Run(ctx, install.Options{
		ReleaseDir: rel, AdminEmail: *admin, Bundle: *bundleDir,
		Stdin: os.Stdin, IsTTY: fi != nil && fi.Mode()&os.ModeCharDevice != 0, Out: os.Stdout, Err: os.Stderr,
	})
	if err != nil {
		return 1 // install.Run printed the failure (before the backup key)
	}
	return 0
}

func run(args []string) int {
	if len(args) > 0 {
		if cmd, ok := commands[args[0]]; ok {
			return cmd(args[1:])
		}
	}
	fmt.Fprintln(os.Stderr, usage)
	return 2
}

func main() { os.Exit(run(os.Args[1:])) }
