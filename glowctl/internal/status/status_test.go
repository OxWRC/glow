package status

import (
	"context"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/oxwrc/glow/glowctl/internal/config"
)

type fake struct {
	out string
	err error
}

func (f fake) Run(context.Context, []string, io.Reader) (string, error) { return f.out, f.err }

const nd = `{"Service":"api","State":"running","Health":"healthy"}
{"Service":"proxy","State":"running","Health":""}
{"Service":"init","State":"exited","ExitCode":0,"Health":""}
`

func get(code int, err error) func(context.Context, *config.Config, string, string) (int, error) {
	return func(context.Context, *config.Config, string, string) (int, error) { return code, err }
}

func find(cs []Check, name string) *Check {
	for i := range cs {
		if cs[i].Name == name {
			return &cs[i]
		}
	}
	return nil
}

func TestRunHealthy(t *testing.T) {
	cfg := &config.Config{Domain: "d.org", StateDir: t.TempDir()}
	writeBS(t, cfg.StateDir, okStatus(now.Add(-30*time.Minute)))
	cs, err := Run(context.Background(), Options{Cfg: cfg, Version: "v1", Runner: fake{out: nd}, Get: get(200, nil), Now: clock})
	if err != nil {
		t.Fatal(err)
	}
	if !AllOK(cs) {
		t.Fatalf("%+v", cs)
	}
	if c := find(cs, "version"); c == nil || c.Detail != "v1" {
		t.Fatalf("version: %+v", cs)
	}
}

func TestRunArrayFormatAndUnhealthy(t *testing.T) {
	arr := `[{"Service":"api","State":"running","Health":"unhealthy"},{"Service":"db","State":"exited","ExitCode":1}]`
	cs, err := Run(context.Background(), Options{Cfg: &config.Config{Domain: "d.org", Demo: true}, Runner: fake{out: arr}, Get: get(200, nil)})
	if err != nil {
		t.Fatal(err)
	}
	if AllOK(cs) || find(cs, "api").OK || find(cs, "db").OK {
		t.Fatalf("%+v", cs)
	}
}

func TestRunProxyDownAndComposeError(t *testing.T) {
	cs, _ := Run(context.Background(), Options{Cfg: &config.Config{Domain: "d.org", Demo: true}, Runner: fake{out: nd}, Get: get(502, nil)})
	if AllOK(cs) {
		t.Fatalf("502 should fail: %+v", cs)
	}
	cs, _ = Run(context.Background(), Options{Cfg: &config.Config{Domain: "d.org", Demo: true}, Runner: fake{out: nd}, Get: get(0, errors.New("refused"))})
	if AllOK(cs) {
		t.Fatal("dial error should fail")
	}
	if _, err := Run(context.Background(), Options{Cfg: &config.Config{Domain: "d.org", Demo: true}, Runner: fake{err: errors.New("boom")}}); err == nil {
		t.Fatal("want compose error")
	}
	if cs, _ := Run(context.Background(), Options{Cfg: &config.Config{Domain: "d.org", Demo: true}, Runner: fake{out: ""}, Get: get(200, nil)}); AllOK(cs) {
		t.Fatal("no services must not be OK")
	}
}

func TestOffModeUsesHTTP(t *testing.T) {
	cs, _ := Run(context.Background(), Options{Cfg: &config.Config{Domain: "d.org", TLSMode: "off", Demo: true}, Runner: fake{out: nd}, Get: get(200, nil)})
	if find(cs, "http://d.org/en/login") == nil || find(cs, "https://d.org/en/login") != nil {
		t.Fatalf("%+v", cs)
	}
}

var now = time.Date(2026, 10, 20, 12, 0, 0, 0, time.UTC)

func clock() time.Time { return now }

func stamp(t time.Time) string { return t.UTC().Format(time.RFC3339) }

func okStatus(at time.Time) string {
	return `{"last_backup_at":"` + stamp(at) + `","last_backup_ok":true,"last_backup_error":"",
"last_verify_at":"` + stamp(at) + `","last_verify_ok":true,"last_verify_error":"",
"last_prune_at":"` + stamp(at) + `","last_prune_ok":true,"last_prune_error":""}`
}

func writeBS(t *testing.T, st, body string) {
	t.Helper()
	d := filepath.Join(st, "backup-status")
	if err := os.MkdirAll(d, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(d, "backup-status.json"), []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}

func writeDep(t *testing.T, st string, at time.Time) {
	t.Helper()
	b := `{"version":"v1","deployed_at":"` + stamp(at) + `","demo":false}`
	if err := os.WriteFile(filepath.Join(st, "deployment.json"), []byte(b), 0o644); err != nil {
		t.Fatal(err)
	}
}

// backupChecks runs status on a production cfg and returns the checks plus
// whether everything passed.
func backupChecks(t *testing.T, st string) ([]Check, bool) {
	t.Helper()
	cs, err := Run(context.Background(), Options{Cfg: &config.Config{Domain: "d.org", StateDir: st},
		Runner: fake{out: nd}, Get: get(200, nil), Now: clock})
	if err != nil {
		t.Fatal(err)
	}
	return cs, AllOK(cs)
}

func TestBackupAge(t *testing.T) {
	st := t.TempDir()
	writeDep(t, st, now.Add(-24*time.Hour))
	writeBS(t, st, okStatus(now.Add(-30*time.Minute)))
	if cs, ok := backupChecks(t, st); !ok || find(cs, "backup") == nil {
		t.Fatalf("30 min old should pass: %+v", cs)
	}
	writeBS(t, st, okStatus(now.Add(-3*time.Hour)))
	cs, ok := backupChecks(t, st)
	if ok || find(cs, "backup").OK || !strings.Contains(find(cs, "backup").Detail, "2h") {
		t.Fatalf("3h old should fail: %+v", cs)
	}
}

func TestBackupMissingAndGrace(t *testing.T) {
	st := t.TempDir()
	writeDep(t, st, now.Add(-30*time.Minute))
	if cs, ok := backupChecks(t, st); !ok {
		t.Fatalf("no backup yet within 2h of install should pass: %+v", cs)
	}
	writeDep(t, st, now.Add(-3*time.Hour))
	if cs, ok := backupChecks(t, st); ok || find(cs, "backup").OK {
		t.Fatalf("no backup 3h after install should fail: %+v", cs)
	}
	os.Remove(filepath.Join(st, "deployment.json"))
	if _, ok := backupChecks(t, st); ok {
		t.Fatal("no deployment.json, no status: fail")
	}
}

func TestBackupFailedRunFailsEvenInGrace(t *testing.T) {
	st := t.TempDir()
	writeDep(t, st, now.Add(-10*time.Minute))
	writeBS(t, st, `{"last_backup_at":"`+stamp(now.Add(-5*time.Minute))+`","last_backup_ok":false,"last_backup_error":"backup path full"}`)
	cs, ok := backupChecks(t, st)
	if ok || !strings.Contains(find(cs, "backup").Detail, "backup path full") {
		t.Fatalf("%+v", cs)
	}
}

func TestVerifyFailed(t *testing.T) {
	st := t.TempDir()
	writeDep(t, st, now.Add(-24*time.Hour))
	b := strings.Replace(okStatus(now.Add(-10*time.Minute)), `"last_verify_ok":true,"last_verify_error":""`,
		`"last_verify_ok":false,"last_verify_error":"glow.users empty"`, 1)
	writeBS(t, st, b)
	cs, ok := backupChecks(t, st)
	if c := find(cs, "restore test"); ok || c == nil || c.OK || !strings.Contains(c.Detail, "glow.users empty") {
		t.Fatalf("%+v", cs)
	}
}

func TestVerifyNeverRunAndPruneFailureWarns(t *testing.T) {
	st := t.TempDir()
	writeDep(t, st, now.Add(-24*time.Hour))
	writeBS(t, st, `{"last_backup_at":"`+stamp(now.Add(-10*time.Minute))+`","last_backup_ok":true,"last_backup_error":"",
"last_verify_at":"","last_verify_ok":false,"last_verify_error":"",
"last_prune_at":"`+stamp(now.Add(-9*time.Minute))+`","last_prune_ok":false,"last_prune_error":"lock held"}`)
	cs, ok := backupChecks(t, st)
	p := find(cs, "prune")
	if !ok || p == nil || !p.Warn || !strings.Contains(p.Detail, "lock held") {
		t.Fatalf("never-verified and prune failure must not fail status: %+v", cs)
	}
}

func TestBackupStatusCorruptFails(t *testing.T) {
	st := t.TempDir()
	writeDep(t, st, now.Add(-10*time.Minute))
	writeBS(t, st, "{not json")
	if cs, ok := backupChecks(t, st); ok {
		t.Fatalf("%+v", cs)
	}
}

func TestDemoSkipsBackupChecks(t *testing.T) {
	st := t.TempDir() // no status, no deployment
	cs, err := Run(context.Background(), Options{Cfg: &config.Config{Domain: "d.org", StateDir: st, Demo: true},
		Runner: fake{out: nd}, Get: get(200, nil), Now: clock})
	if err != nil || !AllOK(cs) || find(cs, "backup") != nil || find(cs, "restore test") != nil {
		t.Fatalf("%v %+v", err, cs)
	}
}

func TestGraceOnlyBeforeFirstBackup(t *testing.T) {
	st := t.TempDir()
	writeDep(t, st, now.Add(-10*time.Minute))
	writeBS(t, st, okStatus(now.Add(-3*time.Hour)))
	if cs, ok := backupChecks(t, st); ok || find(cs, "backup").OK {
		t.Fatalf("stale backup must fail despite recent deployment: %+v", cs)
	}
}

func TestBackupInFutureFails(t *testing.T) {
	st := t.TempDir()
	writeDep(t, st, now.Add(-24*time.Hour))
	writeBS(t, st, okStatus(now.Add(10*time.Minute)))
	cs, ok := backupChecks(t, st)
	if ok || !strings.Contains(find(cs, "backup").Detail, "clock skew?") {
		t.Fatalf("%+v", cs)
	}
	writeBS(t, st, okStatus(now.Add(4*time.Minute)))
	if cs, ok := backupChecks(t, st); !ok {
		t.Fatalf("small skew tolerated: %+v", cs)
	}
}
