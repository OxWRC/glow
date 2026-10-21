package compose

import (
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
