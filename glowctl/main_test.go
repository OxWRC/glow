package main

import (
	"context"
	"io"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"

	"github.com/oxwrc/glow/glowctl/internal/lock"
)

func TestRunDispatch(t *testing.T) {
	for args, want := range map[string]int{"": 2, "bogus": 2, "version": 0, "install": 1} {
		var a []string
		if args != "" {
			a = []string{args}
		}
		if got := run(a); got != want {
			t.Errorf("run(%q)=%d want %d", args, got, want)
		}
	}
}

func TestSplitDir(t *testing.T) {
	d, rest, err := splitDir([]string{"add", "--dir", "/x", "a@b.org", "--admin"})
	if err != nil || d != "/x" || len(rest) != 3 || rest[0] != "add" || rest[2] != "--admin" {
		t.Fatalf("%q %q %v", d, rest, err)
	}
	if d, _, _ := splitDir([]string{"--dir=/y"}); d != "/y" {
		t.Fatal(d)
	}
	if _, _, err := splitDir([]string{"--dir"}); err == nil {
		t.Fatal("want error")
	}
}

func TestInstallRejectsPositionalArgs(t *testing.T) {
	if got := run([]string{"install", "extra"}); got != 2 {
		t.Errorf("exit %d want 2", got)
	}
}

func demoDir(t *testing.T) (dir, st string) {
	dir, st = t.TempDir(), t.TempDir()
	env := "GLOW_DOMAIN=example.org\nGLOW_DEMO_MODE=true\nGLOW_STATE_DIR=" + st + "\n"
	if err := os.WriteFile(filepath.Join(dir, "glow.env"), []byte(env), 0o600); err != nil {
		t.Fatal(err)
	}
	return dir, st
}

func TestStatusAndUsersBeforeInstall(t *testing.T) {
	dir, st := demoDir(t)
	cfg, err := loadValid(dir)
	if err != nil {
		t.Fatal(err)
	}
	if err := requireInstalled(cfg); err == nil || !strings.Contains(err.Error(), "not installed; run glowctl install") {
		t.Fatalf("got %v", err)
	}
	if run([]string{"status", "--dir", dir}) != 1 || run([]string{"users", "list", "--dir", dir}) != 1 {
		t.Error("want exit 1 before install")
	}
	os.WriteFile(filepath.Join(st, "runtime.env"), nil, 0o600)
	if err := requireInstalled(cfg); err != nil {
		t.Fatal(err)
	}
}

func TestLoadValidIndentsEveryError(t *testing.T) {
	d := t.TempDir()
	os.WriteFile(filepath.Join(d, "glow.env"), []byte("GLOW_TLS_MODE=bad\nGLOW_TYPO=1\n"), 0o600)
	_, err := loadValid(d)
	for _, l := range strings.Split(strings.TrimPrefix(err.Error(), "glow.env is invalid:\n"), "\n") {
		if !strings.HasPrefix(l, "  ") {
			t.Errorf("line not indented: %q", l)
		}
	}
}

func TestBackupCmd(t *testing.T) {
	dir, st := demoDir(t)
	if got := run([]string{"backup", "--dir", dir, "extra"}); got != 2 {
		t.Errorf("positional: exit %d want 2", got)
	}
	if got := run([]string{"backup", "--dir", dir}); got != 1 {
		t.Errorf("before install: exit %d want 1", got)
	}
	os.WriteFile(filepath.Join(st, "runtime.env"), nil, 0o600)
	if got := run([]string{"backup", "--dir", dir, "--verify"}); got != 1 {
		t.Errorf("demo: exit %d want 1 (no backups in demo)", got)
	}
}

type recRunner struct{ calls []string }

func (r *recRunner) Run(_ context.Context, args []string, _ io.Reader) (string, error) {
	r.calls = append(r.calls, strings.Join(args, " "))
	return "", nil
}

func TestRunBackupLocksAndMapsArgv(t *testing.T) {
	st := t.TempDir()
	r := &recRunner{}
	if err := runBackup(context.Background(), st, r, true, io.Discard); err != nil || len(r.calls) != 1 ||
		r.calls[0] != "exec -T backup glow-backup.sh verify" {
		t.Fatalf("%v %v", err, r.calls)
	}
	release, err := lock.Acquire(st)
	if err != nil {
		t.Fatal(err)
	}
	defer release()
	err = runBackup(context.Background(), st, r, false, io.Discard)
	if err == nil || !strings.Contains(err.Error(), "another glowctl command is running") || len(r.calls) != 1 {
		t.Fatalf("%v %v", err, r.calls)
	}
}

func TestRestoreUsageAndKeyFile(t *testing.T) {
	if got := run([]string{"restore"}); got != 2 {
		t.Fatalf("restore without --from: %d", got)
	}
	r, w, _ := os.Pipe()
	stderr := os.Stderr
	os.Stderr = w
	got := run([]string{"restore", "--from", t.TempDir()})
	os.Stderr = stderr
	w.Close()
	msg, _ := io.ReadAll(r)
	if got != 1 || !strings.Contains(string(msg), "not a restic repository") {
		t.Fatalf("restore from a non-repo: %d %q", got, msg)
	}
	f := filepath.Join(t.TempDir(), "k")
	os.WriteFile(f, []byte("abc123\n"), 0o600)
	if k, err := readKey(f); err != nil || k != "abc123" {
		t.Fatalf("%q %v", k, err)
	}
	os.WriteFile(f, []byte("\n"), 0o600)
	if _, err := readKey(f); err == nil {
		t.Fatal("empty key accepted")
	}
}

func TestUpdateUsage(t *testing.T) {
	for _, a := range [][]string{{"update"}, {"update", "v1.2.3", "extra"}, {"update", "v1.2.3", "--bogus"}, {"update", "v1.2.3", "--bundle"}} {
		if got := run(a); got != 2 {
			t.Errorf("run(%q)=%d want 2", a, got)
		}
	}
	// Valid usage, but no glow.env in the dir: a plain failure, not usage.
	if got := run([]string{"update", "v1.2.3", "--yes", "--dir", t.TempDir()}); got != 1 {
		t.Errorf("update without glow.env = %d want 1", got)
	}
}

func TestUpdateSignalsIncludeHangup(t *testing.T) {
	for _, s := range updateSignals {
		if s == syscall.SIGHUP {
			return
		}
	}
	t.Fatal("update does not handle SIGHUP: a dropped SSH session would kill it mid-swap")
}

// stderrOf runs args with stderr captured.
func stderrOf(t *testing.T, args ...string) (int, string) {
	t.Helper()
	r, w, _ := os.Pipe()
	saved := os.Stderr
	os.Stderr = w
	got := run(args)
	os.Stderr = saved
	w.Close()
	msg, _ := io.ReadAll(r)
	return got, string(msg)
}

func TestInstanceMismatchRefusedByEveryCommand(t *testing.T) {
	dir, st := demoDir(t)
	os.WriteFile(filepath.Join(st, "runtime.env"), nil, 0o600)
	os.WriteFile(filepath.Join(st, ".glow-instance"), []byte("glow-demo\n"), 0o644)
	for _, a := range [][]string{{"status"}, {"backup"}, {"users", "list"}, {"update", "v9.9.9", "--yes"}} {
		code, msg := stderrOf(t, append(a, "--dir", dir)...)
		if code != 1 || !strings.Contains(msg, "belongs to project glow-demo; GLOW_INSTANCE cannot change after install") {
			t.Errorf("%v: exit %d %q", a, code, msg)
		}
	}
}
