package backup

import (
	"context"
	"errors"
	"io"
	"strings"
	"testing"
)

type rec struct {
	args []string
	out  string
	err  error
}

func (r *rec) Run(_ context.Context, args []string, _ io.Reader) (string, error) {
	r.args = args
	return r.out, r.err
}

func TestNowAndVerifyArgv(t *testing.T) {
	r := &rec{out: "2026-10-20T00:00:00Z backup ok\n"}
	var out strings.Builder
	if err := Now(context.Background(), r, &out); err != nil {
		t.Fatal(err)
	}
	if strings.Join(r.args, " ") != "exec -T backup glow-backup.sh backup" || !strings.Contains(out.String(), "backup ok") {
		t.Fatalf("%q %q", r.args, out.String())
	}
	if err := Verify(context.Background(), r, io.Discard); err != nil {
		t.Fatal(err)
	}
	if strings.Join(r.args, " ") != "exec -T backup glow-backup.sh verify" {
		t.Fatalf("%q", r.args)
	}
}

func TestNowError(t *testing.T) {
	r := &rec{out: "partial\n", err: errors.New("exit status 1: restic: repo locked")}
	var out strings.Builder
	err := Now(context.Background(), r, &out)
	if err == nil || !strings.Contains(err.Error(), "repo locked") || !strings.Contains(out.String(), "partial") {
		t.Fatalf("%v %q", err, out.String())
	}
}

func TestNowSnapshot(t *testing.T) {
	r := &rec{out: "2026-10-20T00:00:00Z backup ok\nsnapshot_id=0123abcd\n2026-10-20T00:00:01Z prune ok\n"}
	var out strings.Builder
	id, err := NowSnapshot(context.Background(), r, &out)
	if err != nil || id != "0123abcd" || !strings.Contains(out.String(), "backup ok") {
		t.Fatalf("%q %v %q", id, err, out.String())
	}
	r.out = "2026-10-20T00:00:00Z backup ok\n" // a backup image without the id line
	if _, err := NowSnapshot(context.Background(), r, io.Discard); err == nil || !strings.Contains(err.Error(), "snapshot id") {
		t.Fatalf("err = %v", err)
	}
	r.out, r.err = "", errors.New("boom")
	if _, err := NowSnapshot(context.Background(), r, io.Discard); err == nil || !strings.Contains(err.Error(), "boom") {
		t.Fatalf("err = %v", err)
	}
}
