package odk

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"
)

type call struct {
	args  []string
	stdin string
}
type fakeRunner struct {
	calls []call
	fail  func(args []string) (string, error)
}

func (f *fakeRunner) Run(_ context.Context, args []string, stdin io.Reader) (string, error) {
	b, _ := io.ReadAll(stdin)
	f.calls = append(f.calls, call{args, string(b)})
	if f.fail != nil {
		return f.fail(args)
	}
	return "", nil
}

func (f *fakeRunner) cliCmds() []string {
	var o []string
	for _, c := range f.calls {
		if len(c.args) > 7 && c.args[3] == "node" {
			o = append(o, "-u "+c.args[6]+" "+c.args[7]+" stdin="+strings.TrimSpace(c.stdin))
		}
	}
	return o
}

type fakeODK struct {
	mu          sync.Mutex
	reqs        []string
	hosts       map[string]bool
	projects    string
	users       string
	assigns     string
	assignCode  int
	loginCode   int
	createdProj bool
}

func (f *fakeODK) server() *httptest.Server {
	f.hosts = map[string]bool{}
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		f.mu.Lock()
		defer f.mu.Unlock()
		f.hosts[r.Host] = true
		f.reqs = append(f.reqs, r.Method+" "+r.URL.Path)
		switch {
		case r.Method == "POST" && r.URL.Path == "/v1/sessions":
			if f.loginCode != 0 {
				w.WriteHeader(f.loginCode)
				return
			}
			fmt.Fprint(w, `{"token":"TOK"}`)
		case r.Method == "DELETE" && r.URL.Path == "/v1/sessions/TOK":
			if r.Header.Get("Authorization") != "Bearer TOK" {
				w.WriteHeader(401)
			}
		case r.Method == "GET" && r.URL.Path == "/v1/projects":
			fmt.Fprint(w, f.projects)
		case r.Method == "POST" && r.URL.Path == "/v1/projects":
			f.createdProj = true
			fmt.Fprint(w, `{"id":1,"name":"GLOW Data Collection"}`)
		case r.Method == "GET" && r.URL.Path == "/v1/users":
			fmt.Fprint(w, f.users)
		case r.Method == "POST" && r.URL.Path == "/v1/users":
			fmt.Fprint(w, `{"id":42}`)
		case r.Method == "GET" && r.URL.Path == "/v1/projects/1/assignments":
			fmt.Fprint(w, f.assigns)
		case r.Method == "POST" && strings.HasPrefix(r.URL.Path, "/v1/projects/1/assignments/"):
			if f.assignCode != 0 {
				w.WriteHeader(f.assignCode)
			}
		case r.Method == "GET" && r.URL.Path == "/":
			fmt.Fprint(w, "ok")
		default:
			w.WriteHeader(404)
		}
	}))
}

func (f *fakeODK) count(s string) int {
	n := 0
	for _, r := range f.reqs {
		if r == s {
			n++
		}
	}
	return n
}

const (
	noProj   = `[]`
	oneProj  = `[{"id":1,"name":"GLOW Data Collection"}]`
	apiUsers = `[{"id":42,"email":"api@x"}]`
)

func run(t *testing.T, f *fakeODK, r *fakeRunner) (int, error) {
	srv := f.server()
	t.Cleanup(srv.Close)
	c := NewClientForBase(srv.URL, "odk.example.org")
	return ConfigureProduction(context.Background(), r, c, "admin@x", "adminpw", "api@x", "apipw")
}

func TestFreshInstall(t *testing.T) {
	f := &fakeODK{projects: noProj, users: `[]`, assigns: `[]`}
	r := &fakeRunner{}
	pid, err := run(t, f, r)
	if err != nil || pid != 1 {
		t.Fatalf("pid=%d err=%v", pid, err)
	}
	if !f.hosts["odk.example.org"] || len(f.hosts) != 1 {
		t.Fatalf("hosts %v", f.hosts)
	}
	if !f.createdProj || f.count("POST /v1/users") != 1 || f.count("POST /v1/projects/1/assignments/2/42") != 1 {
		t.Fatalf("reqs %v", f.reqs)
	}
	if f.count("DELETE /v1/sessions/TOK") != 1 {
		t.Fatalf("session not deleted: %v", f.reqs)
	}
	want := []string{"-u admin@x user-create stdin=adminpw", "-u admin@x user-promote stdin="}
	got := r.cliCmds()
	if strings.Join(got, "|") != strings.Join(want, "|") {
		t.Fatalf("cli %v", got)
	}
	for _, c := range r.calls {
		for _, a := range c.args {
			if a == "adminpw" || a == "apipw" {
				t.Fatal("password in argv")
			}
		}
	}
}

func TestRerunIdempotent(t *testing.T) {
	f := &fakeODK{projects: oneProj, users: apiUsers, assigns: `[{"actorId":42,"roleId":2}]`}
	r := &fakeRunner{fail: func(a []string) (string, error) {
		if a[7] == "user-create" {
			return "", errors.New("exit 1: User already exists")
		}
		return "", nil
	}}
	if _, err := run(t, f, r); err != nil {
		t.Fatal(err)
	}
	if f.createdProj || f.count("POST /v1/users") != 0 || f.count("POST /v1/projects/1/assignments/2/42") != 0 {
		t.Fatalf("not idempotent: %v", f.reqs)
	}
	got := strings.Join(r.cliCmds(), "|")
	if !strings.Contains(got, "-u admin@x user-set-password stdin=adminpw") ||
		!strings.Contains(got, "-u api@x user-set-password stdin=apipw") {
		t.Fatalf("cli %s", got)
	}
}

func TestAssignConflictOK(t *testing.T) {
	f := &fakeODK{projects: oneProj, users: apiUsers, assigns: `[]`, assignCode: 409}
	if _, err := run(t, f, &fakeRunner{}); err != nil {
		t.Fatal(err)
	}
}

func TestAssign500Error(t *testing.T) {
	f := &fakeODK{projects: oneProj, users: apiUsers, assigns: `[]`, assignCode: 500}
	_, err := run(t, f, &fakeRunner{})
	if err == nil || !strings.Contains(err.Error(), "500") {
		t.Fatalf("err %v", err)
	}
	if f.count("DELETE /v1/sessions/TOK") != 1 {
		t.Fatal("session not deleted on error")
	}
}

func TestProjectIDNotOne(t *testing.T) {
	f := &fakeODK{projects: `[{"id":3,"name":"GLOW Data Collection"}]`}
	_, err := run(t, f, &fakeRunner{})
	if err == nil || !strings.Contains(err.Error(), "id 3") {
		t.Fatalf("err %v", err)
	}
}

func TestProjectExactName(t *testing.T) {
	f := &fakeODK{projects: `[{"id":5,"name":"GLOW Data Collection 2"}]`, users: apiUsers, assigns: `[]`}
	if _, err := run(t, f, &fakeRunner{}); err != nil || !f.createdProj {
		t.Fatalf("err %v created %v", err, f.createdProj)
	}
}

func TestLoginErrorStatus(t *testing.T) {
	f := &fakeODK{loginCode: 401}
	_, err := run(t, f, &fakeRunner{})
	if err == nil || !strings.Contains(err.Error(), "401") {
		t.Fatalf("err %v", err)
	}
}

func TestAdminCreateOtherErrorFails(t *testing.T) {
	r := &fakeRunner{fail: func(a []string) (string, error) { return "", errors.New("boom") }}
	_, err := run(t, &fakeODK{}, r)
	if err == nil {
		t.Fatal("want error")
	}
}

func TestWaitReady(t *testing.T) {
	n := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if n++; n < 3 {
			w.WriteHeader(502)
		}
	}))
	defer srv.Close()
	c := NewClientForBase(srv.URL, "odk.x")
	if err := WaitReady(context.Background(), c, 5, time.Millisecond, io.Discard, time.Hour); err != nil || n != 3 {
		t.Fatalf("n=%d err=%v", n, err)
	}
	if err := WaitReady(context.Background(), NewClientForBase("http://127.0.0.1:1", "x"), 2, time.Millisecond, io.Discard, time.Hour); err == nil {
		t.Fatal("want error")
	}
}

func TestDemo(t *testing.T) {
	f := &fakeODK{projects: `[{"id":1,"name":"GLOW Development"}]`}
	srv := f.server()
	defer srv.Close()
	r := &fakeRunner{}
	c := NewClientForBase(srv.URL, "odk.x")
	if err := ConfigureDemo(context.Background(), r, c, "admin@glow.local", "pw"); err != nil {
		t.Fatal(err)
	}
	if len(r.calls) != 1 || r.calls[0].args[2] != "postgres14" || !strings.Contains(r.calls[0].stdin, `UPDATE forms SET "enketoId" = NULL, "enketoOnceId" = NULL;`) ||
		!strings.Contains(r.calls[0].stdin, "upgrade.process.form.draft") {
		t.Fatalf("calls %+v", r.calls)
	}
	if f.count("DELETE /v1/sessions/TOK") != 1 {
		t.Fatal("no logout")
	}
	f.projects = `[{"id":2,"name":"GLOW Development"}]`
	if err := ConfigureDemo(context.Background(), r, c, "a", "p"); err == nil {
		t.Fatal("want id error")
	}
}

func TestPromoteFailureSurfaced(t *testing.T) {
	r := &fakeRunner{fail: func(a []string) (string, error) {
		if a[7] == "user-promote" {
			return "", errors.New("docker: odk-service is not running")
		}
		return "", nil
	}}
	_, err := run(t, &fakeODK{}, r)
	if err == nil || !strings.Contains(err.Error(), "user-promote") {
		t.Fatalf("err %v", err)
	}
}

func TestPromoteAlreadyTolerated(t *testing.T) {
	f := &fakeODK{projects: oneProj, users: apiUsers, assigns: `[{"actorId":42,"roleId":2}]`}
	r := &fakeRunner{fail: func(a []string) (string, error) {
		if a[7] == "user-promote" {
			return "", errors.New("exit 1: A resource already exists")
		}
		return "", nil
	}}
	if _, err := run(t, f, r); err != nil {
		t.Fatal(err)
	}
}

func TestUserByEmailCaseInsensitive(t *testing.T) {
	f := &fakeODK{users: `[{"id":42,"email":"API@X"}]`}
	srv := f.server()
	defer srv.Close()
	id, err := NewClientForBase(srv.URL, "h").UserByEmail(context.Background(), "TOK", "api@x")
	if err != nil || id != 42 {
		t.Fatalf("id=%d err=%v", id, err)
	}
}

func TestLogoutErrorRedactsToken(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(500) }))
	srv.Close() // connection error path includes the URL
	err := NewClientForBase(srv.URL, "h").Logout(context.Background(), "SECRETTOKEN")
	if err == nil || strings.Contains(err.Error(), "SECRETTOKEN") {
		t.Fatalf("err %v", err)
	}
	srv2 := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(500) }))
	defer srv2.Close()
	err = NewClientForBase(srv2.URL, "h").Logout(context.Background(), "SECRETTOKEN")
	if err == nil || strings.Contains(err.Error(), "SECRETTOKEN") {
		t.Fatalf("err %v", err)
	}
}

func TestWaitReadyReportsProgress(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(502) }))
	defer srv.Close()
	var out strings.Builder
	err := WaitReady(context.Background(), NewClientForBase(srv.URL, "odk.x"), 20, 2*time.Millisecond, &out, 5*time.Millisecond)
	if err == nil {
		t.Fatal("want error")
	}
	n := strings.Count(out.String(), "[glowctl] still waiting for ODK Central (")
	if n < 2 || !strings.Contains(out.String(), "): ") {
		t.Fatalf("want >=2 progress lines, got %d:\n%s", n, out.String())
	}
}
