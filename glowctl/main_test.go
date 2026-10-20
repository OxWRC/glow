package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
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
