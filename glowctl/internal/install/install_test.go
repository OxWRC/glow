package install

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/oxwrc/glow/glowctl/internal/config"
	"github.com/oxwrc/glow/glowctl/internal/lock"
	"github.com/oxwrc/glow/glowctl/internal/odk"
)

// fakeRunner records compose args; `glow-api users` state is kept so reruns
// see the admin created by the first run.
type fakeRunner struct {
	calls      []string
	stdins     map[string]string
	glowUsers  []string
	failConfig string // stdout of a failing `config check`
	fail       func(s string) error
}

func (f *fakeRunner) Run(_ context.Context, args []string, stdin io.Reader) (string, error) {
	s := strings.Join(args, " ")
	f.calls = append(f.calls, s)
	if stdin != nil {
		b, _ := io.ReadAll(stdin)
		if f.stdins == nil {
			f.stdins = map[string]string{}
		}
		f.stdins[s] = string(b)
	}
	if f.fail != nil {
		if err := f.fail(s); err != nil {
			return "", err
		}
	}
	switch {
	case strings.HasSuffix(s, "config check"):
		if f.failConfig != "" {
			return f.failConfig, errors.New("exit status 1")
		}
		return "Config OK\n", nil
	case strings.HasSuffix(s, "glow-api users list"):
		return strings.Join(f.glowUsers, "\n"), nil
	case strings.Contains(s, "glow-api users create "):
		f.glowUsers = append(f.glowUsers, args[len(args)-2])
	}
	return "", nil
}

// fakeODK is a tiny stateful ODK Central.
type fakeODK struct {
	mu       sync.Mutex
	projects []string
	users    []string
	assigned map[int]bool
	forms    map[string]string // xmlFormId -> version
	posts    []string          // mutating requests other than sessions
}

func newFakeODK(seededProject string, seededUsers ...string) *fakeODK {
	f := &fakeODK{assigned: map[int]bool{}, forms: map[string]string{}, users: seededUsers}
	if seededProject != "" {
		f.projects = []string{seededProject}
	}
	return f
}

func (f *fakeODK) handler(w http.ResponseWriter, r *http.Request) {
	f.mu.Lock()
	defer f.mu.Unlock()
	p := r.URL.Path
	if r.Method != "GET" && !strings.HasPrefix(p, "/v1/sessions") {
		f.posts = append(f.posts, r.Method+" "+p)
	}
	switch {
	case r.Method == "GET" && p == "/":
		fmt.Fprint(w, "ODK Central")
	case p == "/v1/sessions":
		fmt.Fprint(w, `{"token":"TOK"}`)
	case strings.HasPrefix(p, "/v1/sessions/"):
	case r.Method == "GET" && p == "/v1/projects":
		var ps []map[string]any
		for i, n := range f.projects {
			ps = append(ps, map[string]any{"id": i + 1, "name": n})
		}
		json.NewEncoder(w).Encode(ps)
	case r.Method == "POST" && p == "/v1/projects":
		var b struct{ Name string }
		json.NewDecoder(r.Body).Decode(&b)
		f.projects = append(f.projects, b.Name)
		fmt.Fprintf(w, `{"id":%d}`, len(f.projects))
	case r.Method == "GET" && p == "/v1/users":
		var us []map[string]any
		for i, e := range f.users {
			us = append(us, map[string]any{"id": 10 + i, "email": e})
		}
		json.NewEncoder(w).Encode(us)
	case r.Method == "POST" && p == "/v1/users":
		var b struct{ Email string }
		json.NewDecoder(r.Body).Decode(&b)
		f.users = append(f.users, b.Email)
		fmt.Fprintf(w, `{"id":%d}`, 10+len(f.users)-1)
	case r.Method == "GET" && p == "/v1/projects/1/assignments":
		var as []map[string]any
		for a := range f.assigned {
			as = append(as, map[string]any{"actorId": a, "roleId": 2})
		}
		json.NewEncoder(w).Encode(as)
	case r.Method == "POST" && strings.HasPrefix(p, "/v1/projects/1/assignments/2/"):
		var a int
		fmt.Sscanf(strings.TrimPrefix(p, "/v1/projects/1/assignments/2/"), "%d", &a)
		f.assigned[a] = true
	case r.Method == "POST" && p == "/v1/projects/1/forms":
		b, _ := io.ReadAll(r.Body)
		id, ver := formIDVer(string(b))
		if _, ok := f.forms[id]; ok {
			w.WriteHeader(409)
			return
		}
		f.forms[id] = ver
	case r.Method == "GET" && strings.HasPrefix(p, "/v1/projects/1/forms/"):
		fmt.Fprintf(w, `{"version":%q}`, f.forms[strings.TrimPrefix(p, "/v1/projects/1/forms/")])
	default:
		w.WriteHeader(404)
	}
}

var idVerRe = regexp.MustCompile(`id="([^"]+)" version="([^"]+)"`)

func formIDVer(xml string) (string, string) {
	m := idVerRe.FindStringSubmatch(xml)
	return m[1], m[2]
}

type env struct {
	rel, st string
	odk     *fakeODK
	srv     *httptest.Server
	run     *fakeRunner
	gets    []string
}

func setup(t *testing.T, demo bool) *env {
	t.Helper()
	e := &env{rel: t.TempDir(), st: t.TempDir(), run: &fakeRunner{}}
	backup := t.TempDir()
	glowEnv := "GLOW_DOMAIN=example.org\nGLOW_STATE_DIR=" + e.st + "\n"
	if demo {
		glowEnv += "GLOW_DEMO_MODE=true\n"
		e.odk = newFakeODK("GLOW Development", "admin@glow.local", "api@glow.local")
	} else {
		glowEnv += "GLOW_OIDC_ISSUER=https://idp.example.org\nGLOW_OIDC_CLIENT_ID=glow\nGLOW_BACKUP_PATH=" + backup + "\n"
		e.odk = newFakeODK("")
	}
	must(t, os.WriteFile(filepath.Join(e.rel, "glow.env"), []byte(glowEnv), 0o644))
	must(t, os.WriteFile(filepath.Join(e.rel, "VERSION"), []byte("v1.2.3\n"), 0o644))
	must(t, os.MkdirAll(filepath.Join(e.rel, "odk-forms"), 0o755))
	must(t, os.WriteFile(filepath.Join(e.rel, "odk-forms", "a.xml"),
		[]byte(`<h:html><model><instance><data id="form_a" version="1"/></instance></model></h:html>`), 0o644))
	e.srv = httptest.NewServer(http.HandlerFunc(e.odk.handler))
	t.Cleanup(e.srv.Close)
	return e
}

func must(t *testing.T, err error) {
	t.Helper()
	if err != nil {
		t.Fatal(err)
	}
}

func (e *env) opts(out io.Writer) Options {
	return Options{
		ReleaseDir: e.rel,
		AdminEmail: "boss@example.org",
		Runner:     e.run,
		ODK:        func(*config.Config) *odk.Client { return odk.NewClientForBase(e.srv.URL, "odk.example.org") },
		Get: func(_ context.Context, _ *config.Config, host, path string) (int, error) {
			e.gets = append(e.gets, host+path)
			return 200, nil
		},
		Out:      out,
		Interval: time.Millisecond,
	}
}

func adminPw(t *testing.T, st string) string {
	b, err := os.ReadFile(filepath.Join(st, "secrets.env"))
	must(t, err)
	for _, l := range strings.Split(string(b), "\n") {
		if v, ok := strings.CutPrefix(l, "ODK_ADMIN_PASSWORD="); ok {
			return v
		}
	}
	t.Fatal("no admin password")
	return ""
}

const cli = "exec -T odk-service node /usr/odk/lib/bin/cli.js -u "

func TestProductionHappyPathOrder(t *testing.T) {
	e := setup(t, false)
	var out strings.Builder
	if err := Run(context.Background(), e.opts(&out)); err != nil {
		t.Fatalf("%v\n%s", err, out.String())
	}
	want := []string{
		"run --rm --no-deps api glow-api config check",
		"up -d --pull missing",
		cli + "glow-admin@example.org user-create",
		cli + "glow-admin@example.org user-promote",
		"exec -T api glow-api users list",
		"exec -T api glow-api users create boss@example.org --admin",
		"exec -T backup glow-backup.sh backup",
	}
	if strings.Join(e.run.calls, "\n") != strings.Join(want, "\n") {
		t.Fatalf("compose calls:\n%s\nwant:\n%s", strings.Join(e.run.calls, "\n"), strings.Join(want, "\n"))
	}
	if got := e.run.stdins[cli+"glow-admin@example.org user-create"]; got != adminPw(t, e.st)+"\n" {
		t.Fatal("admin password not passed on stdin")
	}
	if e.odk.users[0] != "glow-api@example.org" || !e.odk.assigned[10] || e.odk.forms["form_a"] != "1" {
		t.Fatalf("odk not configured: users=%v assigned=%v forms=%v", e.odk.users, e.odk.assigned, e.odk.forms)
	}
	wantGets := "example.org/en/login api.example.org/health odk.example.org/"
	if g := strings.Join(e.gets, " "); !strings.Contains(g, wantGets) {
		t.Fatalf("verify gets %q", g)
	}
	for _, d := range []string{"glow-postgres", "odk-postgres", "odk-secrets", "odk-transfer",
		"odk-enketo-redis-main", "odk-enketo-redis-cache", "api-audit-logs", "vintages", "caddy-data", "certs", "backup-status"} {
		if fi, err := os.Stat(filepath.Join(e.st, d)); err != nil || !fi.IsDir() {
			t.Errorf("state dir %s missing", d)
		}
	}
	var dep struct {
		Version    string `json:"version"`
		DeployedAt string `json:"deployed_at"`
		Demo       *bool  `json:"demo"`
		ReleaseDir string `json:"release_dir"`
	}
	b, err := os.ReadFile(filepath.Join(e.st, "deployment.json"))
	must(t, err)
	must(t, json.Unmarshal(b, &dep))
	if _, perr := time.Parse(time.RFC3339, dep.DeployedAt); dep.Version != "v1.2.3" || dep.Demo == nil || *dep.Demo || dep.ReleaseDir == "" ||
		perr != nil || !strings.HasSuffix(dep.DeployedAt, "Z") {
		t.Fatalf("deployment.json %s", b)
	}
	if fi, _ := os.Stat(filepath.Join(e.st, "runtime.env")); fi == nil || fi.Mode().Perm() != 0o600 {
		t.Fatal("runtime.env not written 0600")
	}
	o := out.String()
	if !strings.Contains(o, "[glowctl] ") || strings.Contains(o, adminPw(t, e.st)) {
		t.Fatalf("output missing steps or leaks secret:\n%s", o)
	}
}

func TestConfigCheckFailureAbortsBeforeUp(t *testing.T) {
	e := setup(t, false)
	e.run.failConfig = "GLOW_OIDC_ISSUER: unreachable\n"
	var out strings.Builder
	err := Run(context.Background(), e.opts(&out))
	if err == nil {
		t.Fatal("want error")
	}
	if !strings.Contains(out.String()+err.Error(), "GLOW_OIDC_ISSUER: unreachable") {
		t.Fatalf("API message not shown: %v\n%s", err, out.String())
	}
	for _, c := range e.run.calls {
		if strings.HasPrefix(c, "up") {
			t.Fatal("up ran after failed config check")
		}
	}
}

func TestMissingAdminEmailWithoutTTY(t *testing.T) {
	e := setup(t, false)
	o := e.opts(io.Discard)
	o.AdminEmail = ""
	if err := Run(context.Background(), o); err == nil || !strings.Contains(err.Error(), "--admin-email") {
		t.Fatalf("want --admin-email error, got %v", err)
	}
	if len(e.run.calls) != 0 {
		t.Fatalf("compose called: %v", e.run.calls)
	}
}

func TestAdminEmailPromptOnTTY(t *testing.T) {
	e := setup(t, false)
	o := e.opts(io.Discard)
	o.AdminEmail, o.IsTTY, o.Stdin = "", true, strings.NewReader("tty@example.org\n")
	must(t, Run(context.Background(), o))
	if last := e.run.calls[len(e.run.calls)-2]; last != "exec -T api glow-api users create tty@example.org --admin" {
		t.Fatalf("last call %q", last)
	}
}

func TestRerunIsIdempotent(t *testing.T) {
	e := setup(t, false)
	must(t, Run(context.Background(), e.opts(io.Discard)))
	secrets1, _ := os.ReadFile(filepath.Join(e.st, "secrets.env"))
	posts1 := len(e.odk.posts)
	e.run.calls = nil
	must(t, Run(context.Background(), e.opts(io.Discard)))
	secrets2, _ := os.ReadFile(filepath.Join(e.st, "secrets.env"))
	if string(secrets1) != string(secrets2) {
		t.Fatal("secrets rotated on rerun")
	}
	if extra := e.odk.posts[posts1:]; len(extra) != 0 {
		t.Fatalf("rerun mutated ODK: %v", extra)
	}
	for _, c := range e.run.calls {
		if strings.Contains(c, "users create") {
			t.Fatal("rerun recreated the glow admin")
		}
	}
	if len(e.odk.users) != 1 || len(e.odk.projects) != 1 {
		t.Fatalf("duplicates: users=%v projects=%v", e.odk.users, e.odk.projects)
	}
}

func TestDemoPath(t *testing.T) {
	e := setup(t, true)
	o := e.opts(io.Discard)
	o.AdminEmail = ""
	must(t, Run(context.Background(), o))
	calls := strings.Join(e.run.calls, "\n")
	order := []string{
		"run --rm --no-deps api glow-api config check",
		"stop nginx",
		"up -d --pull missing odk-service",
		"exec -T odk-service node -e",
		cli + "admin@glow.local user-set-password",
		cli + "api@glow.local user-set-password",
		"up -d --pull missing\n",
		"exec -T postgres14 psql",
		"exec -T api rm -f /cache/data.parquet /cache/data.etag",
		"restart api",
		"run --rm glow-init",
	}
	pos := 0
	for _, w := range order {
		i := strings.Index(calls[pos:]+"\n", w)
		if i < 0 {
			t.Fatalf("missing or out of order %q in:\n%s", w, calls)
		}
		pos += i + len(strings.TrimSuffix(w, "\n"))
	}
	if strings.Contains(calls, "users create") || strings.Contains(calls, "user-create") {
		t.Fatal("demo must not create admins")
	}
	if e.run.stdins[cli+"admin@glow.local user-set-password"] != adminPw(t, e.st)+"\n" {
		t.Fatal("demo admin password not rotated to the generated secret")
	}
	b, _ := os.ReadFile(filepath.Join(e.st, "deployment.json"))
	if !strings.Contains(string(b), `"demo":true`) {
		t.Fatalf("deployment.json %s", b)
	}
}

func TestWaitsForAPIBeforeCreatingAdmin(t *testing.T) {
	e := setup(t, false)
	o := e.opts(io.Discard)
	healthy, healthCalls := false, 0
	o.Get = func(_ context.Context, _ *config.Config, host, path string) (int, error) {
		if host == "api.example.org" && path == "/health" {
			if healthCalls++; healthCalls < 3 {
				return 503, nil
			}
			healthy = true
		}
		return 200, nil
	}
	e.run.fail = func(s string) error {
		if strings.Contains(s, "glow-api users") && !healthy {
			return errors.New(`relation "users" does not exist`)
		}
		return nil
	}
	must(t, Run(context.Background(), o))
	n := 0
	for _, c := range e.run.calls {
		if strings.Contains(c, "users create boss@example.org") {
			n++
		}
	}
	if n != 1 {
		t.Fatalf("admin created %d times", n)
	}
}

func TestDemoRotationFailureMentionsNginx(t *testing.T) {
	e := setup(t, true)
	o := e.opts(io.Discard)
	o.AdminEmail = ""
	e.run.fail = func(s string) error {
		if strings.HasSuffix(s, "user-set-password") {
			return errors.New("boom")
		}
		return nil
	}
	err := Run(context.Background(), o)
	if err == nil || !strings.Contains(err.Error(), "nginx is stopped; rerun install") {
		t.Fatalf("got %v", err)
	}
}

func TestDemoIgnoresAdminEmailWithNotice(t *testing.T) {
	e := setup(t, true)
	var out strings.Builder
	must(t, Run(context.Background(), e.opts(&out))) // opts sets AdminEmail
	if !strings.Contains(out.String(), "--admin-email is ignored in demo mode") {
		t.Fatalf("no notice:\n%s", out.String())
	}
}

func TestPrintsODKAdminLocationNotValue(t *testing.T) {
	e := setup(t, false)
	var out strings.Builder
	must(t, Run(context.Background(), e.opts(&out)))
	want := "ODK Central admin: glow-admin@example.org — password: ODK_ADMIN_PASSWORD in " + filepath.Join(e.st, "secrets.env")
	if !strings.Contains(out.String(), want) || strings.Contains(out.String(), adminPw(t, e.st)) {
		t.Fatalf("missing line or leaks value:\n%s", out.String())
	}
	d := setup(t, true)
	var dout strings.Builder
	must(t, Run(context.Background(), d.opts(&dout)))
	if strings.Contains(dout.String(), "ODK Central admin") {
		t.Error("demo must not print production admin line")
	}
}

func failingGets(e *env, o *Options, api bool) {
	o.Progress = 5 * time.Millisecond
	o.Get = func(_ context.Context, _ *config.Config, host, path string) (int, error) {
		if api && host == "api.example.org" {
			return 0, errors.New("connection refused")
		}
		return 200, nil
	}
}

func TestFinalVerifyShowsProgressAndAcmeHint(t *testing.T) {
	e := setup(t, false)
	var out strings.Builder
	o := e.opts(&out)
	failingGets(e, &o, true)
	o.Interval = 2 * time.Millisecond
	err := Run(context.Background(), o)
	if err == nil {
		t.Fatal("want failure")
	}
	if n := strings.Count(out.String(), "[glowctl] still waiting for the API (migrations) ("); n < 2 {
		t.Fatalf("want progress lines, got %d:\n%s", n, out.String())
	}
	if !strings.Contains(out.String(), "connection refused") {
		t.Errorf("progress lacks last error:\n%s", out.String())
	}
	for _, w := range []string{"DNS", "api.example.org", "odk.example.org", "80/443", "docker compose logs proxy"} {
		if !strings.Contains(err.Error(), w) {
			t.Errorf("hint missing %q: %v", w, err)
		}
	}
}

func TestNoAcmeHintInOffMode(t *testing.T) {
	e := setup(t, false)
	f, _ := os.OpenFile(filepath.Join(e.rel, "glow.env"), os.O_APPEND|os.O_WRONLY, 0)
	f.WriteString("GLOW_TLS_MODE=off\n")
	f.Close()
	o := e.opts(io.Discard)
	failingGets(e, &o, true)
	o.Interval = time.Millisecond
	err := Run(context.Background(), o)
	if err == nil || strings.Contains(err.Error(), "DNS") {
		t.Fatalf("want error without hint, got %v", err)
	}
}

func secret(t *testing.T, st, key string) string {
	b, err := os.ReadFile(filepath.Join(st, "secrets.env"))
	must(t, err)
	for _, l := range strings.Split(string(b), "\n") {
		if v, ok := strings.CutPrefix(l, key+"="); ok {
			return v
		}
	}
	return ""
}

const keyMsg = "Store this backup key somewhere other than this server. Without it, backups cannot be restored."

func TestBackupKeyPrintedExactlyOnce(t *testing.T) {
	e := setup(t, false)
	var out1, out2 strings.Builder
	must(t, Run(context.Background(), e.opts(&out1)))
	must(t, Run(context.Background(), e.opts(&out2)))
	key := secret(t, e.st, "GLOW_BACKUP_KEY")
	if len(key) != 64 {
		t.Fatalf("key %q", key)
	}
	all := out1.String() + out2.String()
	if strings.Count(all, key) != 1 || strings.Count(all, keyMsg) != 1 || !strings.Contains(out1.String(), key) {
		t.Fatalf("key must be printed once, in the first run:\n--1--\n%s\n--2--\n%s", out1.String(), out2.String())
	}
	for _, k := range []string{"GLOW_SECRET_KEY", "POSTGRES_PASSWORD", "ODK_POSTGRES_PASSWORD", "ODK_API_PASSWORD", "ODK_ADMIN_PASSWORD"} {
		if strings.Contains(all, secret(t, e.st, k)) {
			t.Errorf("%s leaked", k)
		}
	}
}

// An install from before backups existed gains a key on its next install,
// printed then and never again; existing secrets are untouched.
func TestExistingInstallGainsBackupKey(t *testing.T) {
	e := setup(t, false)
	old := "GLOW_SECRET_KEY=a\nPOSTGRES_PASSWORD=b\nODK_POSTGRES_PASSWORD=c\nODK_ADMIN_PASSWORD=d\nODK_API_PASSWORD=e\n"
	must(t, os.WriteFile(filepath.Join(e.st, "secrets.env"), []byte(old), 0o600))
	var out strings.Builder
	must(t, Run(context.Background(), e.opts(&out)))
	key := secret(t, e.st, "GLOW_BACKUP_KEY")
	if key == "" || !strings.Contains(out.String(), key) || secret(t, e.st, "POSTGRES_PASSWORD") != "b" {
		t.Fatalf("key=%q\n%s", key, out.String())
	}
	var out2 strings.Builder
	must(t, Run(context.Background(), e.opts(&out2)))
	if strings.Contains(out2.String(), key) || secret(t, e.st, "GLOW_BACKUP_KEY") != key {
		t.Fatal("key reprinted or regenerated")
	}
}

func TestRuntimeEnvHasBackupKeyAndPath(t *testing.T) {
	e := setup(t, false)
	must(t, Run(context.Background(), e.opts(io.Discard)))
	b, _ := os.ReadFile(filepath.Join(e.st, "runtime.env"))
	if !strings.Contains(string(b), "GLOW_BACKUP_KEY="+secret(t, e.st, "GLOW_BACKUP_KEY")+"\n") ||
		!strings.Contains(string(b), "GLOW_BACKUP_PATH=/") {
		t.Fatalf("%s", b)
	}
}

func TestDemoRunsNoBackup(t *testing.T) {
	e := setup(t, true)
	must(t, Run(context.Background(), e.opts(io.Discard)))
	for _, c := range e.run.calls {
		if strings.Contains(c, "backup") {
			t.Fatalf("demo ran %q", c)
		}
	}
}

func TestFirstBackupFailureFailsInstallAfterDeployment(t *testing.T) {
	e := setup(t, false)
	e.run.fail = func(s string) error {
		if strings.Contains(s, "glow-backup.sh") {
			return errors.New("restic: repository not writable")
		}
		return nil
	}
	err := Run(context.Background(), e.opts(io.Discard))
	if err == nil || !strings.Contains(err.Error(), "first backup") || !strings.Contains(err.Error(), "not writable") {
		t.Fatalf("got %v", err)
	}
	if _, serr := os.Stat(filepath.Join(e.st, "deployment.json")); serr != nil {
		t.Fatal("deployment.json must be written before the first backup")
	}
}

func TestInstallRefusesWhileLocked(t *testing.T) {
	e := setup(t, false)
	release, err := lock.Acquire(e.st)
	must(t, err)
	defer release()
	err = Run(context.Background(), e.opts(io.Discard))
	if err == nil || !strings.Contains(err.Error(), "another glowctl command is running") || len(e.run.calls) != 0 {
		t.Fatalf("got %v, calls %v", err, e.run.calls)
	}
}

func TestBackupKeyPrintedLastEvenOnFailure(t *testing.T) {
	e := setup(t, false)
	e.run.failConfig = "boom\n"
	var out strings.Builder
	if err := Run(context.Background(), e.opts(&out)); err == nil {
		t.Fatal("want error")
	}
	key := secret(t, e.st, "GLOW_BACKUP_KEY")
	o := strings.TrimRight(out.String(), "\n")
	if strings.Count(o, key) != 1 || !strings.HasSuffix(o, key) || !strings.Contains(o, keyMsg) {
		t.Fatalf("key not last:\n%s", o)
	}
	var out2 strings.Builder
	must(t, func() error { e.run.failConfig = ""; return Run(context.Background(), e.opts(&out2)) }())
	if strings.Contains(out2.String(), key) {
		t.Fatal("reprinted")
	}
}

func TestBackupKeyPrintedLastOnSuccess(t *testing.T) {
	e := setup(t, false)
	var out strings.Builder
	must(t, Run(context.Background(), e.opts(&out)))
	if !strings.HasSuffix(strings.TrimRight(out.String(), "\n"), secret(t, e.st, "GLOW_BACKUP_KEY")) {
		t.Fatalf("key not last:\n%s", out.String())
	}
}

func TestDemoDoesNotPrintBackupKey(t *testing.T) {
	e := setup(t, true)
	var out strings.Builder
	must(t, Run(context.Background(), e.opts(&out)))
	if strings.Contains(out.String(), keyMsg) || strings.Contains(out.String(), secret(t, e.st, "GLOW_BACKUP_KEY")) {
		t.Fatalf("demo printed key:\n%s", out.String())
	}
}

// A demo state dir switched to production shows its (demo-created) key on the
// first production install, then never again.
func TestDemoThenProductionShowsKeyOnce(t *testing.T) {
	e := setup(t, true)
	var out0 strings.Builder
	must(t, Run(context.Background(), e.opts(&out0)))
	key := secret(t, e.st, "GLOW_BACKUP_KEY")
	if strings.Contains(out0.String(), key) {
		t.Fatal("demo printed key")
	}
	if _, err := os.Stat(filepath.Join(e.st, ".backup-key-shown")); err == nil {
		t.Fatal("demo created the marker")
	}
	prod := "GLOW_DOMAIN=example.org\nGLOW_STATE_DIR=" + e.st + "\nGLOW_OIDC_ISSUER=https://idp.example.org\nGLOW_OIDC_CLIENT_ID=glow\nGLOW_BACKUP_PATH=" + t.TempDir() + "\n"
	must(t, os.WriteFile(filepath.Join(e.rel, "glow.env"), []byte(prod), 0o644))
	e.odk.projects = nil // fake ODK: the demo seed's project is not production's
	var out1, out2 strings.Builder
	must(t, Run(context.Background(), e.opts(&out1)))
	o1 := strings.TrimRight(out1.String(), "\n")
	if strings.Count(o1, key) != 1 || !strings.HasSuffix(o1, key) {
		t.Fatalf("first production run must print key last:\n%s", o1)
	}
	if fi, err := os.Stat(filepath.Join(e.st, ".backup-key-shown")); err != nil || fi.Mode().Perm() != 0o600 {
		t.Fatalf("marker: %v", err)
	}
	must(t, Run(context.Background(), e.opts(&out2)))
	if strings.Contains(out2.String(), key) || strings.Contains(out2.String(), keyMsg) {
		t.Fatal("reprinted on second production run")
	}
}

func TestFailureLineBeforeKey(t *testing.T) {
	e := setup(t, false)
	e.run.failConfig = "boom\n"
	var out strings.Builder
	o := e.opts(&out)
	o.Err = &out
	if err := Run(context.Background(), o); err == nil {
		t.Fatal("want error")
	}
	s := out.String()
	f, k := strings.Index(s, "[glowctl] install failed: "), strings.Index(s, keyMsg)
	if f < 0 || k < 0 || f > k || !strings.HasSuffix(strings.TrimRight(s, "\n"), secret(t, e.st, "GLOW_BACKUP_KEY")) {
		t.Fatalf("want failure line then key last:\n%s", s)
	}
}
