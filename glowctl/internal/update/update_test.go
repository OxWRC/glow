package update

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
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
	snapshot string            // restic snapshots output
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
	case strings.Contains(s, "restic snapshots"):
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
}

func newEnv(t *testing.T, demo bool) *env {
	t.Helper()
	e := &env{}
	root := t.TempDir()
	e.parent, e.st = filepath.Join(root, "releases"), filepath.Join(root, "state")
	e.rel = filepath.Join(e.parent, "v1.0.0")
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
	if _, _, err := state.EnsureSecrets(e.st); err != nil {
		t.Fatal(err)
	}
	if err := state.WriteDeployment(e.st, state.Deployment{Version: "v1.0.0", Demo: demo}); err != nil {
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
			"v1.0.0": imagesJSON("reg/glow-api@sha256:a1", "odk/central-service@sha256:o1"),
			"v1.1.0": imagesJSON("reg/glow-api@sha256:a2", "odk/central-service@sha256:o1"),
		},
		hashes:   map[string]string{"reg/glow-api@sha256:a1": "h1", "reg/glow-api@sha256:a2": "h1"},
		snapshot: `[{"id":"snapold","time":"2026-10-21T09:00:00Z"},{"id":"snapnew","time":"2026-10-21T10:00:00Z"}]`,
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
		"v1.1.0 runtime.env.next: pull",
		"v1.1.0 runtime.env.next: run --rm --no-deps api glow-api config check",
		"v1.0.0 runtime.env: config --format json",
		"v1.1.0 runtime.env.next: config --format json",
		"docker run --rm -e GLOW_DEMO_MODE=true reg/glow-api@sha256:a1 glow-api suppression-hash",
		"docker run --rm -e GLOW_DEMO_MODE=true reg/glow-api@sha256:a2 glow-api suppression-hash",
		"v1.0.0 runtime.env: exec -T backup glow-backup.sh backup",
		"v1.0.0 runtime.env: exec -T backup restic snapshots --json --tag glow --host glow --latest 1",
		"v1.1.0 runtime.env: up -d",
	}
	if got := e.calls(); strings.Join(got, "\n") != strings.Join(want, "\n") {
		t.Fatalf("calls:\n%s\nwant:\n%s", strings.Join(got, "\n"), strings.Join(want, "\n"))
	}
	newDir := filepath.Join(e.parent, "v1.1.0")
	if d, err := state.ReadDeployment(e.st); err != nil || d.Version != "v1.1.0" {
		t.Fatalf("deployment.json = %+v, %v", d, err)
	}
	if v := e.runtimeVersion(t); v != "v1.1.0" {
		t.Fatalf("runtime.env GLOW_VERSION = %q", v)
	}
	if b, err := os.ReadFile(filepath.Join(newDir, "glow.env")); err != nil || string(b) != "GLOW_DOMAIN=example.org\n" {
		t.Fatalf("glow.env not copied: %q %v", b, err)
	}
	if fi, err := os.Stat(filepath.Join(newDir, "glowctl")); err != nil || fi.Mode().Perm()&0o100 == 0 {
		t.Fatalf("glowctl not executable: %v %v", fi, err)
	}
	if _, err := os.Stat(filepath.Join(e.rel, "compose.yml")); err != nil {
		t.Fatal("old release dir was not kept")
	}
	if _, err := os.Stat(filepath.Join(e.st, "runtime.env.next")); !os.IsNotExist(err) {
		t.Fatal("runtime.env.next left behind (holds secrets)")
	}
	es, _ := os.ReadDir(e.parent)
	if len(es) != 2 {
		t.Fatalf("parent holds %d entries, want 2 (old + new)", len(es))
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
	if es, _ := os.ReadDir(e.parent); len(es) != 1 {
		t.Fatalf("parent holds %d entries, want only the running release", len(es))
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
	if es, _ := os.ReadDir(e.parent); len(es) != 1 {
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
	e.f.images["v1.0.0.2"] = e.f.images["v1.0.0"]
	if err := Run(context.Background(), e.o); err != nil {
		t.Fatalf("Run: %v\n%s", err, e.out.String())
	}
	if _, err := os.Stat(filepath.Join(e.parent, "v1.0.0.2", "VERSION")); err != nil {
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
	if _, err := os.Stat(filepath.Join(e.parent, "v1.1.0")); !os.IsNotExist(err) {
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
	if !e.has("v1.1.0 runtime.env: up -d") {
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
		if s == "v1.1.0 runtime.env: up -d" {
			swapped = true
		}
		if swapped && s == "v1.0.0 runtime.env: up -d" {
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
		if calls[i] == "v1.1.0 runtime.env: up -d" {
			break
		}
	}
	tail := strings.Join(calls[i+1:], "\n")
	want := strings.Join([]string{
		"v1.0.0 runtime.env: up -d",
		"v1.0.0 runtime.env: stop api",
		"v1.0.0 runtime.env: exec -T backup glow-backup.sh rollback-dbs snapnew glow",
		"v1.0.0 runtime.env: up -d",
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
	if !strings.Contains(err.Error(), filepath.Join(e.parent, "v1.1.0")) {
		t.Errorf("error does not name the failed release dir: %v", err)
	}
}

func TestUpFailureRollsBackBothDBsWhenODKChanged(t *testing.T) {
	e := newEnv(t, false)
	e.f.images["v1.1.0"] = imagesJSON("reg/glow-api@sha256:a2", "odk/central-service@sha256:o2")
	e.f.failOn["v1.1.0 runtime.env: up -d"] = errors.New("container exited")
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
	e.f.failOn["v1.1.0 runtime.env: up -d"] = errors.New("container exited")
	e.f.failOn["rollback-dbs"] = errors.New("pg_restore failed")
	err := Run(context.Background(), e.o)
	if err == nil || !strings.Contains(err.Error(), "rollback") || !strings.Contains(err.Error(), "pg_restore failed") {
		t.Fatalf("err = %v", err)
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
