package odk

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"testing"
)

func formXML(id, ver string) string {
	return fmt.Sprintf(`<?xml version="1.0"?>
<h:html xmlns="http://www.w3.org/2002/xforms" xmlns:h="http://www.w3.org/1999/xhtml"><h:head><h:title>t</h:title><model><instance><data id="%s" version="%s"><a/></data></instance><instance id="x"><root><item/></root></instance></model></h:head><h:body/></h:html>
`, id, ver)
}

type formODK struct {
	mu        sync.Mutex
	reqs      []string
	bodies    map[string]string
	published map[string]string // id -> version
	failID    string            // create returns 500 for this id
	pubCode   int               // override draft/publish status
	hdrs      http.Header
}

func (f *formODK) server() *httptest.Server {
	f.bodies = map[string]string{}
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		f.mu.Lock()
		defer f.mu.Unlock()
		f.reqs = append(f.reqs, r.Method+" "+r.URL.Path+"?"+r.URL.RawQuery)
		b, _ := io.ReadAll(r.Body)
		body := string(b)
		p := r.URL.Path
		switch {
		case r.Method == "POST" && p == "/v1/projects/1/forms":
			f.hdrs = r.Header.Clone()
			id := between(body, `<data id="`)
			if id == f.failID {
				w.WriteHeader(500)
				fmt.Fprint(w, `{"message":"boom from odk"}`)
				return
			}
			if _, ok := f.published[id]; ok {
				w.WriteHeader(409)
				return
			}
			f.published[id] = between(body, `" version="`)
		case r.Method == "GET" && strings.HasPrefix(p, "/v1/projects/1/forms/"):
			id := strings.TrimPrefix(p, "/v1/projects/1/forms/")
			v, ok := f.published[id]
			if !ok {
				w.WriteHeader(404)
				return
			}
			fmt.Fprintf(w, `{"xmlFormId":%q,"version":%q}`, id, v)
		case r.Method == "POST" && strings.HasSuffix(p, "/draft"):
			f.bodies[p] = body
		case r.Method == "POST" && strings.HasSuffix(p, "/draft/publish"):
			if f.pubCode != 0 {
				w.WriteHeader(f.pubCode)
				return
			}
			id := strings.TrimSuffix(strings.TrimPrefix(p, "/v1/projects/1/forms/"), "/draft/publish")
			f.published[id] = between(f.bodies["/v1/projects/1/forms/"+id+"/draft"], `" version="`)
		case r.Method == "DELETE" && strings.HasSuffix(p, "/draft"):
		default:
			w.WriteHeader(404)
		}
	}))
}

func between(s, after string) string {
	i := strings.Index(s, after)
	if i < 0 {
		return ""
	}
	s = s[i+len(after):]
	return s[:strings.Index(s, `"`)]
}

func writeForms(t *testing.T, files map[string]string) (dir, state string) {
	t.Helper()
	dir = t.TempDir()
	for n, c := range files {
		if err := os.WriteFile(filepath.Join(dir, n), []byte(c), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	return dir, filepath.Join(t.TempDir(), "odk-forms-state.json")
}

func TestSyncFormsNewUnchangedAndMode(t *testing.T) {
	f := &formODK{published: map[string]string{}}
	srv := f.server()
	defer srv.Close()
	c := NewClientForBase(srv.URL, "odk.x")
	dir, st := writeForms(t, map[string]string{"a.xml": formXML("a", "1"), "notes.txt": "x"})
	up, err := SyncForms(context.Background(), c, "TOK", 1, dir, st)
	if err != nil || !reflect.DeepEqual(up, []string{"a"}) {
		t.Fatalf("up=%v err=%v", up, err)
	}
	if f.reqs[0] != "POST /v1/projects/1/forms?publish=true" || len(f.reqs) != 1 {
		t.Fatalf("reqs=%v", f.reqs)
	}
	if f.hdrs.Get("Content-Type") != "application/xml" || f.hdrs.Get("X-XmlFormId-Fallback") != "true" || f.hdrs.Get("Authorization") != "Bearer TOK" {
		t.Fatalf("hdrs=%v", f.hdrs)
	}
	if fi, _ := os.Stat(st); fi.Mode().Perm() != 0o600 {
		t.Fatalf("mode %v", fi.Mode())
	}
	f.reqs = nil
	up, err = SyncForms(context.Background(), c, "TOK", 1, dir, st)
	if err != nil || len(up) != 0 || len(f.reqs) != 0 {
		t.Fatalf("rerun up=%v err=%v reqs=%v", up, err, f.reqs)
	}
}

func TestSyncFormsNewVersionPublishes(t *testing.T) {
	f := &formODK{published: map[string]string{"a": "1"}}
	srv := f.server()
	defer srv.Close()
	c := NewClientForBase(srv.URL, "odk.x")
	dir, st := writeForms(t, map[string]string{"a.xml": formXML("a", "2")})
	up, err := SyncForms(context.Background(), c, "TOK", 1, dir, st)
	if err != nil || !reflect.DeepEqual(up, []string{"a"}) {
		t.Fatalf("up=%v err=%v", up, err)
	}
	want := []string{
		"POST /v1/projects/1/forms?publish=true",
		"GET /v1/projects/1/forms/a?",
		"POST /v1/projects/1/forms/a/draft?ignoreWarnings=true",
		"POST /v1/projects/1/forms/a/draft/publish?",
	}
	if !reflect.DeepEqual(f.reqs, want) {
		t.Fatalf("reqs=%v", f.reqs)
	}
	if f.published["a"] != "2" {
		t.Fatalf("published=%v", f.published)
	}
}

func TestSyncFormsSameVersionIsUnchanged(t *testing.T) {
	// State lost but ODK already has this version: no draft, hash recorded.
	f := &formODK{published: map[string]string{"a": "1"}}
	srv := f.server()
	defer srv.Close()
	c := NewClientForBase(srv.URL, "odk.x")
	dir, st := writeForms(t, map[string]string{"a.xml": formXML("a", "1")})
	up, err := SyncForms(context.Background(), c, "TOK", 1, dir, st)
	if err != nil || len(up) != 0 {
		t.Fatalf("up=%v err=%v", up, err)
	}
	for _, r := range f.reqs {
		if strings.Contains(r, "draft") {
			t.Fatalf("draft created: %v", f.reqs)
		}
	}
	b, _ := os.ReadFile(st)
	if !strings.Contains(string(b), `"a"`) {
		t.Fatalf("state=%s", b)
	}
}

func TestSyncFormsPublish409IsUnchanged(t *testing.T) {
	f := &formODK{published: map[string]string{"a": "1"}, pubCode: 409}
	srv := f.server()
	defer srv.Close()
	c := NewClientForBase(srv.URL, "odk.x")
	dir, st := writeForms(t, map[string]string{"a.xml": formXML("a", "2")})
	if _, err := SyncForms(context.Background(), c, "TOK", 1, dir, st); err != nil {
		t.Fatal(err)
	}
	if got := f.reqs[len(f.reqs)-1]; got != "DELETE /v1/projects/1/forms/a/draft?" {
		t.Fatalf("orphan draft not deleted: %v", f.reqs)
	}
	if b, _ := os.ReadFile(st); !strings.Contains(string(b), `"a"`) {
		t.Fatalf("state=%s", b)
	}
}

func TestSyncFormsSameIDDedupedAndIdempotent(t *testing.T) {
	f := &formODK{published: map[string]string{}}
	srv := f.server()
	defer srv.Close()
	c := NewClientForBase(srv.URL, "odk.x")
	dir, st := writeForms(t, map[string]string{
		"b_v1.xml": formXML("b", "1"), "b_v2.xml": formXML("b", "2"), "z.xml": formXML("z", "1"),
	})
	up, err := SyncForms(context.Background(), c, "TOK", 1, dir, st)
	if err != nil || !reflect.DeepEqual(up, []string{"b", "z"}) {
		t.Fatalf("up=%v err=%v", up, err)
	}
	if f.published["b"] != "2" {
		t.Fatalf("pub=%v", f.published)
	}
	b, _ := os.ReadFile(st)
	want := fmt.Sprintf(`"hash": "%s"`, hashForm([]byte(formXML("b", "2"))))
	if !strings.Contains(string(b), want) {
		t.Fatalf("state=%s", b)
	}
	f.reqs = nil
	up, err = SyncForms(context.Background(), c, "TOK", 1, dir, st)
	if err != nil || len(up) != 0 || len(f.reqs) != 0 {
		t.Fatalf("rerun up=%v err=%v reqs=%v", up, err, f.reqs)
	}
}

func TestSyncFormsPartialStateAndErrorBody(t *testing.T) {
	f := &formODK{published: map[string]string{}, failID: "z"}
	srv := f.server()
	defer srv.Close()
	c := NewClientForBase(srv.URL, "odk.x")
	dir, st := writeForms(t, map[string]string{"a.xml": formXML("a", "1"), "z.xml": formXML("z", "1")})
	_, err := SyncForms(context.Background(), c, "TOK", 1, dir, st)
	if err == nil || !strings.Contains(err.Error(), "boom from odk") || strings.Contains(err.Error(), "TOK") {
		t.Fatalf("err=%v", err)
	}
	b, _ := os.ReadFile(st)
	if !strings.Contains(string(b), `"a"`) || strings.Contains(string(b), `"z"`) {
		t.Fatalf("state=%s", b)
	}
}

func TestSyncFormsMissingVersion(t *testing.T) {
	f := &formODK{published: map[string]string{}}
	srv := f.server()
	defer srv.Close()
	c := NewClientForBase(srv.URL, "odk.x")
	dir, st := writeForms(t, map[string]string{"n.xml": strings.Replace(formXML("n", "1"), ` version="1"`, "", 1)})
	_, err := SyncForms(context.Background(), c, "TOK", 1, dir, st)
	if err == nil || !strings.Contains(err.Error(), "n.xml") || !strings.Contains(err.Error(), "no version attribute") {
		t.Fatalf("err=%v", err)
	}
}

func TestHashTrimsTrailingNewlines(t *testing.T) {
	if hashForm([]byte("x\n\n")) != hashForm([]byte("x")) {
		t.Fatal("hash differs")
	}
	// sha256("x")
	if hashForm([]byte("x")) != "2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881" {
		t.Fatal("bad sha")
	}
}

func TestFormIDVersionRealFiles(t *testing.T) {
	want := map[string][2]string{
		"bewell_questionnaire_v1.xml":    {"bewell_questionnaire", "1"},
		"bewell_questionnaire_v2.xml":    {"bewell_questionnaire", "2"},
		"demographics_questionnaire.xml": {"demographics_questionnaire", "1"},
	}
	for n, w := range want {
		b, err := os.ReadFile("../../../odk-forms/" + n)
		if err != nil {
			t.Fatal(err)
		}
		id, v, err := formIDVersion(b)
		if err != nil || id != w[0] || v != w[1] {
			t.Fatalf("%s: %q %q %v", n, id, v, err)
		}
	}
	phq, _ := os.ReadFile("../../../odk-forms/phq9_questionnaire.xml")
	if id, _, err := formIDVersion(phq); err != nil || id == "" {
		t.Fatalf("phq9: %q %v", id, err)
	}
	if _, _, err := formIDVersion([]byte("<a/>")); err == nil {
		t.Fatal("want error")
	}
}
