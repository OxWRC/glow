package update

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"github.com/oxwrc/glow/glowctl/internal/compose"
	"github.com/oxwrc/glow/glowctl/internal/config"
	"github.com/oxwrc/glow/glowctl/internal/install"
	"github.com/oxwrc/glow/glowctl/internal/state"
)

// tarEntry is one archive member; typ 0 means a regular file.
type tarEntry struct {
	name, body string
	typ        byte
	link       string
}

func makeTar(t *testing.T, entries []tarEntry) []byte {
	t.Helper()
	var buf bytes.Buffer
	gz := gzip.NewWriter(&buf)
	tw := tar.NewWriter(gz)
	for _, e := range entries {
		h := &tar.Header{Name: e.name, Mode: 0o644, Size: int64(len(e.body)), Typeflag: tar.TypeReg, Linkname: e.link}
		switch {
		case e.typ != 0:
			h.Typeflag, h.Size, h.Mode = e.typ, 0, 0o755
		case strings.HasSuffix(e.name, "/glowctl"):
			h.Mode = 0o755
		}
		if err := tw.WriteHeader(h); err != nil {
			t.Fatal(err)
		}
		if h.Size > 0 {
			if _, err := tw.Write([]byte(e.body)); err != nil {
				t.Fatal(err)
			}
		}
	}
	if err := tw.Close(); err != nil {
		t.Fatal(err)
	}
	if err := gz.Close(); err != nil {
		t.Fatal(err)
	}
	return buf.Bytes()
}

func releaseArchive(t *testing.T, version string) []byte {
	top := "glow-" + version + "/"
	return makeTar(t, []tarEntry{
		{name: top, typ: tar.TypeDir},
		{name: top + "compose.yml", body: "services: {}\n"},
		{name: top + "compose.demo.yml", body: "services: {}\n"},
		{name: top + "glow.env.example", body: "GLOW_DOMAIN=\n"},
		{name: top + "VERSION", body: version + "\n"},
		{name: top + "DEPLOYMENT.md", body: "# deploy\n"},
		{name: top + "glowctl", body: "#!/bin/sh\n"},
		{name: top + "proxy/", typ: tar.TypeDir},
		{name: top + "proxy/Caddyfile.acme", body: "x\n"},
	})
}

func sha(b []byte) string { s := sha256.Sum256(b); return hex.EncodeToString(s[:]) }

// fake records compose (per release dir and env file) and docker calls in one log.
type fake struct {
	mu       sync.Mutex
	log      []string
	failOn   map[string]error  // a call containing the key fails
	images   map[string]string // release dir base -> compose config json
	hashes   map[string]string // api image -> suppression hash
	snapshot string            // glow-backup.sh backup output
	onCall   func(string)      // observes each call
}

func (f *fake) record(s string) error {
	f.mu.Lock()
	f.log = append(f.log, s)
	f.mu.Unlock()
	if f.onCall != nil {
		f.onCall(s)
	}
	for k, err := range f.failOn {
		if strings.Contains(s, k) {
			return err
		}
	}
	return nil
}

type fakeCompose struct {
	f        *fake
	dir, env string
}

func (c fakeCompose) Run(_ context.Context, args []string, _ io.Reader) (string, error) {
	tag := filepath.Base(c.dir) + " " + filepath.Base(c.env) + ": "
	s := tag + strings.Join(args, " ")
	if err := c.f.record(s); err != nil {
		return "", err
	}
	switch {
	case strings.Join(args, " ") == "config --format json":
		return c.f.images[filepath.Base(c.dir)], nil
	case strings.HasSuffix(s, "glow-backup.sh backup"):
		return c.f.snapshot, nil
	}
	return "", nil
}

type fakeDocker struct{ f *fake }

func (d fakeDocker) Run(_ context.Context, args []string, _ io.Reader) (string, error) {
	s := "docker " + strings.Join(args, " ")
	if err := d.f.record(s); err != nil {
		return "", err
	}
	if len(args) > 4 && args[len(args)-1] == "suppression-hash" {
		img := args[len(args)-3]
		if h, ok := d.f.hashes[img]; ok {
			return "some log line\n" + h + "\n", nil
		}
		return "", errors.New("no such image")
	}
	return "", nil
}

func imagesJSON(api, odk string) string {
	return fmt.Sprintf(`{"name":"glow","services":{"api":{"image":%q},"dashboard":{"image":"reg/glow-dashboard@sha256:d1"},`+
		`"odk-service":{"image":%q},"postgres14":{"image":"reg/glow-odk-postgres@sha256:p1"},"api-db":{"image":"postgres@sha256:g1"}}}`, api, odk)
}

type env struct {
	parent, rel, st string
	f               *fake
	srv             *httptest.Server
	hits            int
	files           map[string][]byte // served by srv under /<version>/
	o               Options
	out             bytes.Buffer
	healthy         func() bool
	forms           func() error
}

func newEnv(t *testing.T, demo bool) *env {
	t.Helper()
	e := &env{}
	root := t.TempDir()
	e.parent, e.st = filepath.Join(root, "releases"), filepath.Join(root, "state")
	e.rel = filepath.Join(e.parent, "glow-v1.0.0")
	for _, d := range []string{e.rel, e.st} {
		if err := os.MkdirAll(d, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	for n, c := range map[string]string{"VERSION": "v1.0.0\n", "compose.yml": "services: {}\n", "glow.env": "GLOW_DOMAIN=example.org\n"} {
		if err := os.WriteFile(filepath.Join(e.rel, n), []byte(c), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.Chmod(filepath.Join(e.rel, "glow.env"), 0o640); err != nil { // the operator's choice
		t.Fatal(err)
	}
	if _, _, err := state.EnsureSecrets(e.st); err != nil {
		t.Fatal(err)
	}
	if err := compose.SwitchCurrent(e.rel); err != nil { // as install does
		t.Fatal(err)
	}
	if err := state.WriteDeployment(e.st, state.Deployment{Version: "v1.0.0", Demo: demo, ReleaseDir: e.rel}); err != nil {
		t.Fatal(err)
	}
	cfg := &config.Config{Domain: "example.org", TLSMode: "acme", HTTPPort: 80, HTTPSPort: 443,
		StateDir: e.st, Demo: demo, SMTP: config.SMTPConfig{Port: 587}}
	if err := state.WriteRuntimeEnv(cfg, mustSecrets(t, e.st), e.rel, filepath.Join(e.st, "runtime.env")); err != nil {
		t.Fatal(err)
	}

	e.f = &fake{
		failOn: map[string]error{},
		images: map[string]string{
			"glow-v1.0.0": imagesJSON("reg/glow-api@sha256:a1", "odk/central-service@sha256:o1"),
			"glow-v1.1.0": imagesJSON("reg/glow-api@sha256:a2", "odk/central-service@sha256:o1"),
		},
		hashes:   map[string]string{"reg/glow-api@sha256:a1": "h1", "reg/glow-api@sha256:a2": "h1"},
		snapshot: "2026-10-21T10:00:00Z backup ok\nsnapshot_id=snapnew\n2026-10-21T10:00:01Z prune ok\n",
	}
	arch := releaseArchive(t, "v1.1.0")
	e.files = map[string][]byte{
		"glow-v1.1.0.tar.gz": arch,
		"SHA256SUMS":         []byte(sha(arch) + "  glow-v1.1.0.tar.gz\n" + strings.Repeat("0", 64) + "  digests.json\n"),
	}
	e.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		e.hits++
		b, ok := e.files[strings.TrimPrefix(r.URL.Path, "/v1.1.0/")]
		if !ok || !strings.HasPrefix(r.URL.Path, "/v1.1.0/") {
			http.NotFound(w, r)
			return
		}
		w.Write(b)
	}))
	t.Cleanup(e.srv.Close)
	e.healthy = func() bool { return true }
	e.forms = func() error { return nil }
	e.o = Options{
		ReleaseDir: e.rel, Cfg: cfg, Version: "v1.1.0", Yes: true,
		BaseURL: e.srv.URL, HTTP: e.srv.Client(),
		Compose: func(dir, envFile string) compose.Runner { return fakeCompose{e.f, dir, envFile} },
		Docker:  fakeDocker{e.f},
		Get: func(context.Context, *config.Config, string, string) (int, error) {
			if e.healthy() {
				return 200, nil
			}
			return 502, nil
		},
		SyncForms: func(_ context.Context, dir string) error {
			if err := e.f.record("forms " + filepath.Base(dir)); err != nil {
				return err
			}
			return e.forms()
		},
		Out: &e.out, Interval: 1, Attempts: 3,
	}
	return e
}

func mustSecrets(t *testing.T, st string) state.Secrets {
	s, _, err := state.EnsureSecrets(st)
	if err != nil {
		t.Fatal(err)
	}
	return s
}

func (e *env) calls() []string {
	e.f.mu.Lock()
	defer e.f.mu.Unlock()
	return append([]string{}, e.f.log...)
}

func (e *env) has(sub string) bool {
	for _, c := range e.calls() {
		if strings.Contains(c, sub) {
			return true
		}
	}
	return false
}

func (e *env) runtimeVersion(t *testing.T) string {
	b, err := os.ReadFile(filepath.Join(e.st, "runtime.env"))
	if err != nil {
		t.Fatal(err)
	}
	for _, l := range strings.Split(string(b), "\n") {
		if v, ok := strings.CutPrefix(l, "GLOW_VERSION="); ok {
			return v
		}
	}
	return ""
}

func TestSuccessArgvOrder(t *testing.T) {
	e := newEnv(t, false)
	if err := Run(context.Background(), e.o); err != nil {
		t.Fatalf("Run: %v\n%s", err, e.out.String())
	}
	want := []string{
		"glow-v1.1.0 runtime.env.next: pull",
		"glow-v1.1.0 runtime.env.next: run --rm --no-deps api glow-api config check",
		"glow-v1.0.0 runtime.env: config --format json",
		"glow-v1.1.0 runtime.env.next: config --format json",
		"docker run --rm -e GLOW_DEMO_MODE=true reg/glow-api@sha256:a1 glow-api suppression-hash",
		"docker run --rm -e GLOW_DEMO_MODE=true reg/glow-api@sha256:a2 glow-api suppression-hash",
		"glow-v1.0.0 runtime.env: exec -T backup glow-backup.sh backup",
		"glow-v1.1.0 runtime.env: up -d --no-deps --force-recreate api",
		"glow-v1.1.0 runtime.env: up -d --remove-orphans",
		"forms glow-v1.1.0",
	}
	if got := e.calls(); strings.Join(got, "\n") != strings.Join(want, "\n") {
		t.Fatalf("calls:\n%s\nwant:\n%s", strings.Join(got, "\n"), strings.Join(want, "\n"))
	}
	newDir := filepath.Join(e.parent, "glow-v1.1.0")
	if d, err := state.ReadDeployment(e.st); err != nil || d.Version != "v1.1.0" || d.ReleaseDir != newDir {
		t.Fatalf("deployment.json = %+v, %v", d, err)
	}
	if v := e.runtimeVersion(t); v != "v1.1.0" {
		t.Fatalf("runtime.env GLOW_VERSION = %q", v)
	}
	if b, err := os.ReadFile(filepath.Join(newDir, "glow.env")); err != nil || string(b) != "GLOW_DOMAIN=example.org\n" {
		t.Fatalf("glow.env not copied: %q %v", b, err)
	}
	// Same mode as the running release's: the backup service mounts it, and a
	// mode change alone would recreate that container.
	if a, b := modeOf(t, filepath.Join(e.rel, "glow.env")), modeOf(t, filepath.Join(newDir, "glow.env")); a != b {
		t.Fatalf("glow.env mode %v, want %v", b, a)
	}
	if fi, err := os.Stat(filepath.Join(newDir, "glowctl")); err != nil || fi.Mode().Perm()&0o100 == 0 {
		t.Fatalf("glowctl not executable: %v %v", fi, err)
	}
	if _, err := os.Stat(filepath.Join(e.rel, "compose.yml")); err != nil {
		t.Fatal("old release dir was not kept")
	}
	if l, err := os.Readlink(filepath.Join(e.parent, "current")); err != nil || l != "glow-v1.1.0" {
		t.Fatalf("current -> %q, %v; want glow-v1.1.0", l, err)
	}
	if _, err := os.Stat(filepath.Join(e.st, "runtime.env.next")); !os.IsNotExist(err) {
		t.Fatal("runtime.env.next left behind (holds secrets)")
	}
	es, _ := os.ReadDir(e.parent)
	if len(es) != 3 {
		t.Fatalf("parent holds %d entries, want 3 (old + new + current)", len(es))
	}
	out := e.out.String()
	for _, w := range []string{newDir, e.rel, "changed services: api"} {
		if !strings.Contains(out, w) {
			t.Errorf("output lacks %q:\n%s", w, out)
		}
	}
	if strings.Contains(out, mustSecrets(t, e.st).BackupKey) || strings.Contains(out, mustSecrets(t, e.st).PostgresPassword) {
		t.Fatal("output contains a secret")
	}
}

func TestChecksumMismatchAbortsBeforeUnpack(t *testing.T) {
	e := newEnv(t, false)
	e.files["SHA256SUMS"] = []byte(strings.Repeat("ab", 32) + "  glow-v1.1.0.tar.gz\n")
	err := Run(context.Background(), e.o)
	if err == nil || !strings.Contains(err.Error(), "checksum") {
		t.Fatalf("err = %v", err)
	}
	if es, _ := os.ReadDir(e.parent); len(es) != 2 {
		t.Fatalf("parent holds %d entries, want only the running release and current", len(es))
	}
	if len(e.calls()) != 0 {
		t.Fatalf("calls after checksum mismatch: %v", e.calls())
	}
}

func TestMissingFromSHA256SUMSAborts(t *testing.T) {
	e := newEnv(t, false)
	e.files["SHA256SUMS"] = []byte(strings.Repeat("ab", 32) + "  other.tar.gz\n")
	if err := Run(context.Background(), e.o); err == nil || !strings.Contains(err.Error(), "SHA256SUMS") {
		t.Fatalf("err = %v", err)
	}
	if es, _ := os.ReadDir(e.parent); len(es) != 2 {
		t.Fatalf("parent holds %d entries", len(es))
	}
}

func TestDowngradeRefused(t *testing.T) {
	e := newEnv(t, false)
	e.o.Version = "v0.9.0"
	err := Run(context.Background(), e.o)
	if err == nil || !strings.Contains(err.Error(), "downgrade") {
		t.Fatalf("err = %v", err)
	}
	if e.hits != 0 || len(e.calls()) != 0 {
		t.Fatalf("downgrade did work: %d downloads, calls %v", e.hits, e.calls())
	}
}

func TestBadVersionRefused(t *testing.T) {
	e := newEnv(t, false)
	e.o.Version = "../v1.1.0"
	if err := Run(context.Background(), e.o); err == nil || e.hits != 0 {
		t.Fatalf("err = %v, hits %d", err, e.hits)
	}
}

func TestSameVersionReapplies(t *testing.T) {
	e := newEnv(t, false)
	e.o.Version = "v1.0.0"
	arch := releaseArchive(t, "v1.0.0")
	e.files = map[string][]byte{"glow-v1.0.0.tar.gz": arch, "SHA256SUMS": []byte(sha(arch) + "  glow-v1.0.0.tar.gz\n")}
	e.srv.Config.Handler = http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if b, ok := e.files[strings.TrimPrefix(r.URL.Path, "/v1.0.0/")]; ok {
			w.Write(b)
			return
		}
		http.NotFound(w, r)
	})
	e.f.images["glow-v1.0.0.2"] = e.f.images["glow-v1.0.0"]
	if err := Run(context.Background(), e.o); err != nil {
		t.Fatalf("Run: %v\n%s", err, e.out.String())
	}
	if _, err := os.Stat(filepath.Join(e.parent, "glow-v1.0.0.2", "VERSION")); err != nil {
		t.Fatalf("re-apply did not unpack beside the running release: %v", err)
	}
}

func TestSuppressionChangeRefusedInProduction(t *testing.T) {
	e := newEnv(t, false)
	e.f.hashes["reg/glow-api@sha256:a2"] = "h2"
	err := Run(context.Background(), e.o)
	if err == nil || !strings.Contains(err.Error(), "suppression") {
		t.Fatalf("err = %v", err)
	}
	if e.has("backup") || e.has(": up") {
		t.Fatalf("went on after the suppression guard: %v", e.calls())
	}
	if _, err := os.Stat(filepath.Join(e.parent, "glow-v1.1.0")); !os.IsNotExist(err) {
		t.Fatal("unused new release dir left behind")
	}
	if v := e.runtimeVersion(t); v != "v1.0.0" {
		t.Fatalf("runtime.env changed: %q", v)
	}
}

func TestSuppressionUnknownRunningRefusedInProduction(t *testing.T) {
	e := newEnv(t, false)
	delete(e.f.hashes, "reg/glow-api@sha256:a1")
	if err := Run(context.Background(), e.o); err == nil || !strings.Contains(err.Error(), "suppression") {
		t.Fatalf("err = %v", err)
	}
	if e.has(": up") {
		t.Fatal("up issued")
	}
}

func TestSuppressionChangeAllowedInDemo(t *testing.T) {
	e := newEnv(t, true)
	e.f.hashes["reg/glow-api@sha256:a2"] = "h2"
	if err := Run(context.Background(), e.o); err != nil {
		t.Fatalf("Run: %v\n%s", err, e.out.String())
	}
	if !strings.Contains(e.out.String(), "suppression rules differ") {
		t.Fatalf("no note:\n%s", e.out.String())
	}
	if e.has("backup") {
		t.Fatalf("demo took a backup: %v", e.calls())
	}
	if !e.has("glow-v1.1.0 runtime.env: up -d") {
		t.Fatal("no up")
	}
}

func TestConfigCheckFailureNoDowntime(t *testing.T) {
	e := newEnv(t, false)
	e.f.failOn["config check"] = errors.New("GLOW_FOO: invalid")
	err := Run(context.Background(), e.o)
	if err == nil || !strings.Contains(err.Error(), "configuration") {
		t.Fatalf("err = %v", err)
	}
	for _, c := range e.calls() {
		if strings.Contains(c, ": up") || strings.Contains(c, "stop") || strings.Contains(c, "backup") {
			t.Fatalf("downtime or backup after failed config check: %v", e.calls())
		}
	}
	if d, _ := state.ReadDeployment(e.st); d.Version != "v1.0.0" {
		t.Fatalf("deployment.json changed: %+v", d)
	}
}

func TestHealthFailureRollsBackGlowDBOnly(t *testing.T) {
	e := newEnv(t, false)
	rolledBack := false
	swapped := false
	e.f.onCall = func(s string) {
		if s == "glow-v1.1.0 runtime.env: up -d --remove-orphans" {
			swapped = true
		}
		if swapped && s == "glow-v1.0.0 runtime.env: up -d --remove-orphans" {
			rolledBack = true
		}
	}
	e.healthy = func() bool { return !swapped || rolledBack }
	err := Run(context.Background(), e.o)
	if err == nil || !strings.Contains(err.Error(), "rolled back to v1.0.0") {
		t.Fatalf("err = %v\n%s", err, e.out.String())
	}
	calls := e.calls()
	i := 0
	for ; i < len(calls); i++ {
		if calls[i] == "glow-v1.1.0 runtime.env: up -d --remove-orphans" {
			break
		}
	}
	tail := strings.Join(calls[i+1:], "\n")
	want := strings.Join([]string{
		"glow-v1.0.0 runtime.env: up -d --no-deps --force-recreate api",
		"glow-v1.0.0 runtime.env: up -d --remove-orphans",
		"glow-v1.0.0 runtime.env: stop api",
		"glow-v1.0.0 runtime.env: exec -T backup glow-backup.sh rollback-dbs snapnew glow",
		"glow-v1.0.0 runtime.env: up -d",
	}, "\n")
	if tail != want {
		t.Fatalf("rollback calls:\n%s\nwant:\n%s", tail, want)
	}
	if v := e.runtimeVersion(t); v != "v1.0.0" {
		t.Fatalf("runtime.env not restored: %q", v)
	}
	if d, _ := state.ReadDeployment(e.st); d.Version != "v1.0.0" {
		t.Fatalf("deployment.json changed: %+v", d)
	}
	if l, _ := os.Readlink(filepath.Join(e.parent, "current")); l != "glow-v1.0.0" {
		t.Fatalf("current -> %q after rollback, want glow-v1.0.0", l)
	}
	if !strings.Contains(err.Error(), filepath.Join(e.parent, "glow-v1.1.0")) {
		t.Errorf("error does not name the failed release dir: %v", err)
	}
}

func TestUpFailureRollsBackBothDBsWhenODKChanged(t *testing.T) {
	e := newEnv(t, false)
	e.f.images["glow-v1.1.0"] = imagesJSON("reg/glow-api@sha256:a2", "odk/central-service@sha256:o2")
	e.f.failOn["glow-v1.1.0 runtime.env: up -d"] = errors.New("container exited")
	err := Run(context.Background(), e.o)
	if err == nil || !strings.Contains(err.Error(), "rolled back") {
		t.Fatalf("err = %v", err)
	}
	if !e.has("stop api odk-service") || !e.has("rollback-dbs snapnew glow odk") {
		t.Fatalf("calls: %v", e.calls())
	}
}

func TestRollbackFailureIsReported(t *testing.T) {
	e := newEnv(t, false)
	e.f.failOn["glow-v1.1.0 runtime.env: up -d"] = errors.New("container exited")
	e.f.failOn["rollback-dbs"] = errors.New("pg_restore failed")
	err := Run(context.Background(), e.o)
	if err == nil || !strings.Contains(err.Error(), "rollback") || !strings.Contains(err.Error(), "pg_restore failed") {
		t.Fatalf("err = %v", err)
	}
	msg := err.Error()
	for _, w := range []string{
		"docker compose --project-name glow --project-directory " + filepath.Join(e.parent, "current"),
		"up -d --no-deps --force-recreate api",
		"--env-file " + filepath.Join(e.st, "runtime.env"),
		"exec -T backup glow-backup.sh rollback-dbs snapnew glow",
		"stop api",
		"glowctl restore",
		"lack rollback-dbs",
	} {
		if !strings.Contains(msg, w) {
			t.Errorf("ALSO FAILED message lacks %q:\n%s", w, msg)
		}
	}
	if strings.Contains(msg, mustSecrets(t, e.st).BackupKey) {
		t.Fatal("message contains the backup key")
	}
}

func TestNoSnapshotIDAbortsBeforeSwap(t *testing.T) {
	e := newEnv(t, false)
	e.f.snapshot = "2026-10-21T10:00:00Z backup ok\n"
	if err := Run(context.Background(), e.o); err == nil || !strings.Contains(err.Error(), "snapshot") {
		t.Fatalf("err = %v", err)
	}
	if e.has(": up") {
		t.Fatal("up without a known pre-update snapshot")
	}
}

func TestBackupFailureAbortsBeforeSwap(t *testing.T) {
	e := newEnv(t, false)
	e.f.failOn["glow-backup.sh backup"] = errors.New("repo full")
	if err := Run(context.Background(), e.o); err == nil || !strings.Contains(err.Error(), "backup") {
		t.Fatalf("err = %v", err)
	}
	if e.has(": up") {
		t.Fatal("up after failed backup")
	}
}

func TestNoConfirmationWithoutYes(t *testing.T) {
	e := newEnv(t, false)
	e.o.Yes = false
	if err := Run(context.Background(), e.o); err == nil || !strings.Contains(err.Error(), "--yes") {
		t.Fatalf("err = %v", err)
	}
	if e.has("backup") || e.has(": up") {
		t.Fatal("went on without confirmation")
	}
	e2 := newEnv(t, false)
	e2.o.Yes, e2.o.IsTTY, e2.o.Stdin = false, true, strings.NewReader("y\n")
	if err := Run(context.Background(), e2.o); err != nil {
		t.Fatalf("confirmed run: %v", err)
	}
}

func TestUnpackRejectsTraversal(t *testing.T) {
	for name, entries := range map[string][]tarEntry{
		"dotdot":   {{name: "glow-v1/../../evil", body: "x"}},
		"absolute": {{name: "/etc/evil", body: "x"}},
		"nested":   {{name: "glow-v1/a/../../../evil", body: "x"}},
		"symlink":  {{name: "glow-v1/link", typ: tar.TypeSymlink, link: "/etc"}},
		"hardlink": {{name: "glow-v1/link", typ: tar.TypeLink, link: "../../etc/passwd"}},
		"toplevel": {{name: "evil", body: "x"}},
	} {
		t.Run(name, func(t *testing.T) {
			root := t.TempDir()
			dst := filepath.Join(root, "out")
			if err := os.Mkdir(dst, 0o755); err != nil {
				t.Fatal(err)
			}
			if err := unpack(bytes.NewReader(makeTar(t, entries)), dst); err == nil {
				t.Fatal("unpack accepted a bad entry")
			}
			if _, err := os.Lstat(filepath.Join(root, "evil")); !os.IsNotExist(err) {
				t.Fatal("file written outside the destination")
			}
		})
	}
}

func TestUnpackStripsTopDir(t *testing.T) {
	dst := t.TempDir()
	if err := unpack(bytes.NewReader(releaseArchive(t, "v1.1.0")), dst); err != nil {
		t.Fatal(err)
	}
	if b, err := os.ReadFile(filepath.Join(dst, "proxy", "Caddyfile.acme")); err != nil || string(b) != "x\n" {
		t.Fatalf("%q %v", b, err)
	}
}

func TestFormsSyncedAfterHealthyUpdate(t *testing.T) {
	e := newEnv(t, true)
	if err := Run(context.Background(), e.o); err != nil {
		t.Fatalf("Run: %v", err)
	}
	if c := e.calls(); c[len(c)-1] != "forms glow-v1.1.0" {
		t.Fatalf("forms not synced last: %v", c)
	}
}

func TestFormsSyncFailureWarnsWithoutRollback(t *testing.T) {
	e := newEnv(t, false)
	e.forms = func() error { return errors.New("odk down") }
	if err := Run(context.Background(), e.o); err != nil {
		t.Fatalf("Run: %v", err)
	}
	if e.has("glow-v1.0.0 runtime.env: up") || e.has("rollback-dbs") {
		t.Fatalf("rolled back on a forms failure: %v", e.calls())
	}
	if out := e.out.String(); !strings.Contains(out, "WARNING") || !strings.Contains(out, "odk down") {
		t.Fatalf("no warning:\n%s", out)
	}
	if d, _ := state.ReadDeployment(e.st); d.Version != "v1.1.0" {
		t.Fatalf("deployment.json = %+v", d)
	}
}

func writeRelease(t *testing.T, dir, version string) {
	t.Helper()
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	for n, c := range map[string]string{"VERSION": version + "\n", "glow.env": "GLOW_DOMAIN=example.org\n"} {
		if err := os.WriteFile(filepath.Join(dir, n), []byte(c), 0o600); err != nil {
			t.Fatal(err)
		}
	}
}

func TestStaleReleaseDirRefused(t *testing.T) {
	e := newEnv(t, false)
	stale := filepath.Join(e.parent, "glow-v0.9.0")
	writeRelease(t, stale, "v0.9.0")
	e.o.ReleaseDir = stale
	err := Run(context.Background(), e.o)
	if err == nil || !strings.Contains(err.Error(), e.rel) {
		t.Fatalf("err = %v", err)
	}
	if e.hits != 0 || len(e.calls()) != 0 {
		t.Fatalf("stale dir did work: %d downloads, %v", e.hits, e.calls())
	}
}

func TestStaleReleaseDirRefusedWithoutRecordedDir(t *testing.T) {
	e := newEnv(t, false)
	// deployment.json without release_dir (older install): the version check
	// still refuses, and the sibling holding the running version is named.
	if err := state.WriteDeployment(e.st, state.Deployment{Version: "v1.0.0"}); err != nil {
		t.Fatal(err)
	}
	stale := filepath.Join(e.parent, "glow-v0.9.0")
	writeRelease(t, stale, "v0.9.0")
	e.o.ReleaseDir = stale
	err := Run(context.Background(), e.o)
	if err == nil || !strings.Contains(err.Error(), e.rel) {
		t.Fatalf("err = %v", err)
	}
}

func TestOtherReleaseDirSameVersionRefused(t *testing.T) {
	e := newEnv(t, false)
	other := filepath.Join(e.parent, "glow-v1.0.0.2")
	writeRelease(t, other, "v1.0.0")
	e.o.ReleaseDir = other
	err := Run(context.Background(), e.o)
	if err == nil || !strings.Contains(err.Error(), e.rel) {
		t.Fatalf("err = %v", err)
	}
	if len(e.calls()) != 0 {
		t.Fatalf("calls: %v", e.calls())
	}
}

func TestCancelAfterSwapStillRollsBack(t *testing.T) {
	e := newEnv(t, false)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	e.f.onCall = func(s string) {
		if s == "glow-v1.1.0 runtime.env: up -d --remove-orphans" {
			cancel() // e.g. SIGHUP from a dropped SSH session
		}
	}
	err := Run(ctx, e.o)
	if err == nil || !strings.Contains(err.Error(), "rolled back to v1.0.0") {
		t.Fatalf("err = %v", err)
	}
	if !e.has("rollback-dbs snapnew glow") {
		t.Fatalf("calls: %v", e.calls())
	}
}

func TestStateDirsCreated(t *testing.T) {
	e := newEnv(t, false)
	if err := Run(context.Background(), e.o); err != nil {
		t.Fatalf("Run: %v\n%s", err, e.out.String())
	}
	for _, d := range append(append([]string{}, install.StateDirs...), "certs") {
		if fi, err := os.Stat(filepath.Join(e.st, d)); err != nil || !fi.IsDir() {
			t.Errorf("state dir %s not created", d)
		}
	}
}

func TestManualHintUsesProject(t *testing.T) {
	e := newEnv(t, false)
	e.o.Cfg.Instance = "demo"
	msg := manual(e.o, rollbackPlan{rtEnv: "/st/runtime.env"}, nil, nil)
	if !strings.Contains(msg, "--project-name glow-demo ") {
		t.Fatalf("%s", msg)
	}
}

func writeFiles(t *testing.T, root string, files map[string]string) {
	t.Helper()
	for n, c := range files {
		p := filepath.Join(root, n)
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(p, []byte(c), 0o644); err != nil {
			t.Fatal(err)
		}
	}
}

// svcJSON is a compose config with one bind per service (source "" = none).
func svcJSON(t *testing.T, svcs map[string][3]string) string {
	t.Helper()
	m := map[string]any{}
	for n, v := range svcs { // image, bind source, env X
		s := map[string]any{"image": v[0], "environment": map[string]string{"X": v[2]}}
		if v[1] != "" {
			s["volumes"] = []map[string]any{{"type": "bind", "source": v[1], "target": "/t", "read_only": true}}
		}
		m[n] = s
	}
	b, err := json.Marshal(map[string]any{"name": "glow", "services": m})
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

func TestRecreateSetFromConfigAndReleaseFiles(t *testing.T) {
	parent := t.TempDir()
	a, b := filepath.Join(parent, "glow-v1.0.0"), filepath.Join(parent, "glow-v1.1.0")
	same := map[string]string{"proxy/Caddyfile": "x", "redis/d/a.conf": "1", "redis/d/sub/b.conf": "2"}
	writeFiles(t, a, same)
	writeFiles(t, b, same)
	writeFiles(t, a, map[string]string{"nginx/odk.conf": "old", "enk/d/c.conf": "1"})
	writeFiles(t, b, map[string]string{"nginx/odk.conf": "new", "enk/d/c.conf": "1", "enk/d/extra.conf": "1"})
	if err := compose.SwitchCurrent(a); err != nil {
		t.Fatal(err)
	}
	cur := filepath.Join(parent, "current")
	// The running stack's config goes through current; the new one's through its real dir.
	oldJ := svcJSON(t, map[string][3]string{
		"api": {"api@a1", "/st/logs", "1"}, "postgres14": {"pg@p1", "", "1"}, "dashboard": {"d@1", "/st/x", "1"},
		"proxy": {"caddy@1", cur + "/proxy/Caddyfile", "1"}, "nginx": {"n@1", cur + "/nginx/odk.conf", "1"},
		"redis": {"r@1", cur + "/redis/d", "1"}, "enketo": {"e@1", cur + "/enk/d", "1"}, "gone": {"g@1", "", "1"},
	})
	newJ := svcJSON(t, map[string][3]string{
		"api": {"api@a2", "/st/logs", "1"}, "postgres14": {"pg@p1", "", "2"}, "dashboard": {"d@1", "/st/x", "1"},
		"proxy": {"caddy@1", b + "/proxy/Caddyfile", "1"}, "nginx": {"n@1", b + "/nginx/odk.conf", "1"},
		"redis": {"r@1", b + "/redis/d", "1"}, "enketo": {"e@1", b + "/enk/d", "1"}, "extra": {"x@1", "", "1"},
	})
	oldS, err := parseServices(oldJ, a, cur)
	if err != nil {
		t.Fatal(err)
	}
	newS, err := parseServices(newJ, b, cur)
	if err != nil {
		t.Fatal(err)
	}
	got, err := recreateSet(oldS, newS, a, b, true)
	if err != nil {
		t.Fatal(err)
	}
	// api: image; postgres14: config; nginx: file content; enketo: dir tree;
	// extra: new service. gone is not running in the new release (no recreate).
	if want := "api enketo extra nginx postgres14"; strings.Join(got, " ") != want {
		t.Fatalf("recreate = %v, want %s", got, want)
	}
	// First update from a deployment without current: every service with
	// release-dir binds moves to the stable path once.
	got, err = recreateSet(oldS, newS, a, b, false)
	if err != nil {
		t.Fatal(err)
	}
	if want := "api enketo extra nginx postgres14 proxy redis"; strings.Join(got, " ") != want {
		t.Fatalf("recreate without current = %v, want %s", got, want)
	}
}

func TestSameTreeModeAndKind(t *testing.T) {
	d := t.TempDir()
	writeFiles(t, d, map[string]string{"a/f": "1", "b/f": "1", "c/f": "1"})
	os.Chmod(filepath.Join(d, "c/f"), 0o600)
	if ok, err := sameTree(filepath.Join(d, "a"), filepath.Join(d, "b")); !ok || err != nil {
		t.Fatalf("equal trees differ: %v", err)
	}
	if ok, _ := sameTree(filepath.Join(d, "a"), filepath.Join(d, "c")); ok {
		t.Fatal("mode change not seen")
	}
	if ok, _ := sameTree(filepath.Join(d, "a"), filepath.Join(d, "a/f")); ok {
		t.Fatal("dir vs file not seen")
	}
	if ok, _ := sameTree(filepath.Join(d, "a"), filepath.Join(d, "missing")); ok {
		t.Fatal("missing not seen")
	}
}

// A release whose only change is a bind-mounted ODK/proxy file recreates
// just that service (and says ODK is interrupted); a GLOW-only one says not.
func TestUpdateRecreatesServiceWhoseReleaseFileChanged(t *testing.T) {
	e := newEnv(t, false)
	newDir := filepath.Join(e.parent, "glow-v1.1.0")
	writeFiles(t, e.rel, map[string]string{"proxy/Caddyfile.acme": "old\n"})
	cur := filepath.Join(e.parent, "current")
	e.f.images["glow-v1.0.0"] = svcJSON(t, map[string][3]string{"api": {"reg/glow-api@sha256:a1", "", "1"},
		"proxy": {"caddy@1", cur + "/proxy/Caddyfile.acme", "1"}})
	e.f.images["glow-v1.1.0"] = svcJSON(t, map[string][3]string{"api": {"reg/glow-api@sha256:a1", "", "1"},
		"proxy": {"caddy@1", newDir + "/proxy/Caddyfile.acme", "1"}})
	if err := Run(context.Background(), e.o); err != nil {
		t.Fatalf("Run: %v\n%s", err, e.out.String())
	}
	if !e.has("glow-v1.1.0 runtime.env: up -d --no-deps --force-recreate proxy") || e.has("--force-recreate api") {
		t.Fatalf("calls: %v", e.calls())
	}
	if out := e.out.String(); !strings.Contains(out, "changed services: proxy") || !strings.Contains(out, "interrupt") {
		t.Fatalf("output: %s", out)
	}
}

func TestGlowOnlyUpdateSaysODKKeepsRunning(t *testing.T) {
	e := newEnv(t, false)
	e.o.Yes, e.o.IsTTY, e.o.Stdin = false, true, strings.NewReader("n\n")
	Run(context.Background(), e.o) //nolint:errcheck // cancelled at the prompt
	if out := e.out.String(); !strings.Contains(out, "api will be recreated") || !strings.Contains(out, "ODK Central and the proxy keep running") {
		t.Fatalf("prompt: %s", out)
	}
}

func TestUpdateRunFromCurrentLink(t *testing.T) {
	e := newEnv(t, false)
	e.o.ReleaseDir = filepath.Join(e.parent, "current")
	if err := Run(context.Background(), e.o); err != nil {
		t.Fatalf("Run: %v\n%s", err, e.out.String())
	}
	newDir := filepath.Join(e.parent, "glow-v1.1.0")
	if d, _ := state.ReadDeployment(e.st); d.ReleaseDir != newDir {
		t.Fatalf("deployment.json release_dir = %q, want the real dir %s", d.ReleaseDir, newDir)
	}
	if !e.has("glow-v1.0.0 runtime.env: exec -T backup glow-backup.sh backup") {
		t.Fatalf("old release not addressed by its real dir: %v", e.calls())
	}
}

// A service new in the failed release is not recreated by the rollback (the
// previous release lacks it; --remove-orphans removes it).
func TestRollbackRecreatesOnlyPreviousServices(t *testing.T) {
	e := newEnv(t, false)
	e.f.images["glow-v1.1.0"] = svcJSON(t, map[string][3]string{"api": {"reg/glow-api@sha256:a2", "", "1"}, "extra": {"x@1", "", "1"}})
	e.f.images["glow-v1.0.0"] = svcJSON(t, map[string][3]string{"api": {"reg/glow-api@sha256:a1", "", "1"}})
	e.f.failOn["glow-v1.1.0 runtime.env: up -d"] = errors.New("container exited")
	if err := Run(context.Background(), e.o); err == nil || !strings.Contains(err.Error(), "rolled back") {
		t.Fatalf("err = %v", err)
	}
	if !e.has("glow-v1.0.0 runtime.env: up -d --no-deps --force-recreate api") || e.has("glow-v1.0.0 runtime.env: up -d --no-deps --force-recreate api extra") {
		t.Fatalf("calls: %v", e.calls())
	}
}

func modeOf(t *testing.T, p string) os.FileMode {
	t.Helper()
	fi, err := os.Stat(p)
	if err != nil {
		t.Fatal(err)
	}
	return fi.Mode()
}
