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
		"odk-enketo-redis-main", "odk-enketo-redis-cache", "api-audit-logs", "vintages", "caddy-data", "certs"} {
		if fi, err := os.Stat(filepath.Join(e.st, d)); err != nil || !fi.IsDir() {
			t.Errorf("state dir %s missing", d)
		}
	}
	var dep struct {
		Version    string `json:"version"`
		DeployedAt string `json:"deployed_at"`
		Demo       *bool  `json:"demo"`
	}
	b, err := os.ReadFile(filepath.Join(e.st, "deployment.json"))
	must(t, err)
	must(t, json.Unmarshal(b, &dep))
	if _, perr := time.Parse(time.RFC3339, dep.DeployedAt); dep.Version != "v1.2.3" || dep.Demo == nil || *dep.Demo ||
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
	if last := e.run.calls[len(e.run.calls)-1]; last != "exec -T api glow-api users create tty@example.org --admin" {
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
