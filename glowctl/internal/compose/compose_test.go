package compose

import (
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

func TestArgv(t *testing.T) {
	base := []string{"docker", "compose", "--project-name", "glow", "--project-directory", "/rel",
		"--env-file", "/st/runtime.env", "-f", "/rel/compose.yml"}
	if got := New("/rel", "/st", "glow", false).Argv("up", "-d"); !reflect.DeepEqual(got, append(append([]string{}, base...), "up", "-d")) {
		t.Fatalf("prod: %v", got)
	}
	want := append(append([]string{}, base...), "-f", "/rel/compose.demo.yml", "ps")
	if got := New("/rel", "/st", "glow", true).Argv("ps"); !reflect.DeepEqual(got, want) {
		t.Fatalf("demo: %v", got)
	}
}

var _ Runner = Exec{}

func TestWithEnvFile(t *testing.T) {
	got := WithEnvFile("/rel", "/st/runtime.env.next", "glow-demo", false).Argv("pull")
	if got[3] != "glow-demo" || got[7] != "/st/runtime.env.next" || got[len(got)-1] != "pull" {
		t.Fatalf("%v", got)
	}
}

func TestArgvProject(t *testing.T) {
	if got := New("/rel", "/st", "glow-demo", false).Argv("ps"); got[2] != "--project-name" || got[3] != "glow-demo" {
		t.Fatalf("%v", got)
	}
}

func mkRelease(t *testing.T, parent, name string) string {
	t.Helper()
	d := filepath.Join(parent, name)
	if err := os.MkdirAll(d, 0o755); err != nil {
		t.Fatal(err)
	}
	return d
}

func TestArgvUsesCurrentWhenItIsThisRelease(t *testing.T) {
	parent := t.TempDir()
	a, b := mkRelease(t, parent, "glow-v1"), mkRelease(t, parent, "glow-v2")
	cur := filepath.Join(parent, "current")
	if got := New(a, "/st", "glow", false).Argv("ps"); got[5] != a {
		t.Fatalf("no current yet: want the real dir, got %v", got)
	}
	if err := SwitchCurrent(a); err != nil {
		t.Fatal(err)
	}
	ca, cb := New(a, "/st", "glow", false), New(b, "/st", "glow", false)
	for _, c := range []*Compose{ca, New(cur, "/st", "glow", false)} {
		if got := c.Argv("ps"); got[5] != cur || got[9] != filepath.Join(cur, "compose.yml") {
			t.Fatalf("current -> v1: want paths through %s, got %v", cur, got)
		}
	}
	if got := cb.Argv("ps"); got[5] != b {
		t.Fatalf("v2 is not current: want its real dir, got %v", got)
	}
	// Switching is atomic (rename over the link) and re-evaluated per call.
	if err := SwitchCurrent(b); err != nil {
		t.Fatal(err)
	}
	if got := cb.Argv("ps"); got[5] != cur {
		t.Fatalf("after switch: %v", got)
	}
	if got := ca.Argv("ps"); got[5] != a {
		t.Fatalf("v1 after switch: %v", got)
	}
	if l, err := os.Readlink(cur); err != nil || l != "glow-v2" {
		t.Fatalf("current -> %q, %v (want relative glow-v2)", l, err)
	}
	// Switching back (rollback), and switching via the link itself, work.
	if err := SwitchCurrent(a); err != nil {
		t.Fatal(err)
	}
	if err := SwitchCurrent(cur); err != nil {
		t.Fatal(err)
	}
	if l, _ := os.Readlink(cur); l != "glow-v1" {
		t.Fatalf("current -> %q after switch back", l)
	}
	es, _ := os.ReadDir(parent)
	if len(es) != 3 {
		t.Fatalf("temp links left behind: %v", es)
	}
}

func TestSwitchCurrentRefusesARealDir(t *testing.T) {
	parent := t.TempDir()
	a := mkRelease(t, parent, "glow-v1")
	mkRelease(t, parent, "current")
	if err := SwitchCurrent(a); err == nil {
		t.Fatal("replaced a real directory named current")
	}
}
