package compose

import (
	"reflect"
	"testing"
)

func TestArgv(t *testing.T) {
	base := []string{"docker", "compose", "--project-name", "glow", "--project-directory", "/rel",
		"--env-file", "/st/runtime.env", "-f", "/rel/compose.yml"}
	if got := New("/rel", "/st", false).Argv("up", "-d"); !reflect.DeepEqual(got, append(append([]string{}, base...), "up", "-d")) {
		t.Fatalf("prod: %v", got)
	}
	want := append(append([]string{}, base...), "-f", "/rel/compose.demo.yml", "ps")
	if got := New("/rel", "/st", true).Argv("ps"); !reflect.DeepEqual(got, want) {
		t.Fatalf("demo: %v", got)
	}
}

var _ Runner = Exec{}
