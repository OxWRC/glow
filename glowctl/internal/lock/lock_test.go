package lock

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestAcquireExclusiveAndRelease(t *testing.T) {
	st := filepath.Join(t.TempDir(), "state")
	rel, err := Acquire(st)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(st, ".glowctl.lock")); err != nil {
		t.Fatal(err)
	}
	if _, err := Acquire(st); err == nil || !strings.Contains(err.Error(), "another glowctl command is running") {
		t.Fatalf("second acquire: %v", err)
	}
	rel()
	rel2, err := Acquire(st)
	if err != nil {
		t.Fatalf("after release: %v", err)
	}
	rel2()
}
