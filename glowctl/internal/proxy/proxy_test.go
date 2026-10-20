package proxy

import (
	"context"
	"net"
	"net/http"
	"net/http/httptest"
	"strconv"
	"testing"

	"github.com/oxwrc/glow/glowctl/internal/config"
)

func TestTargets(t *testing.T) {
	got := Targets(&config.Config{Domain: "d.org"})
	want := [][2]string{{"d.org", "/en/login"}, {"api.d.org", "/health"}, {"odk.d.org", "/"}}
	if len(got) != 3 || got[0] != want[0] || got[1] != want[1] || got[2] != want[2] {
		t.Fatalf("%v", got)
	}
}

func port(t *testing.T, s *httptest.Server) int {
	_, p, _ := net.SplitHostPort(s.Listener.Addr().String())
	n, _ := strconv.Atoi(p)
	return n
}

// Get dials loopback with SNI/Host = host, ignoring the certificate.
func TestGetTLSSendsHostAndSNI(t *testing.T) {
	var host, sni string
	s := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		host, sni = r.Host, r.TLS.ServerName
		w.WriteHeader(204)
	}))
	s.StartTLS()
	defer s.Close()
	code, err := Get(context.Background(), &config.Config{TLSMode: "acme", HTTPSPort: port(t, s)}, "api.d.org", "/health")
	if err != nil || code != 204 || host != "api.d.org" || sni != "api.d.org" {
		t.Fatalf("code=%d err=%v host=%q sni=%q", code, err, host, sni)
	}
}

func TestGetOffModePlainHTTP(t *testing.T) {
	var host string
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { host = r.Host }))
	defer s.Close()
	code, err := Get(context.Background(), &config.Config{TLSMode: "off", HTTPPort: port(t, s)}, "d.org", "/")
	if err != nil || code != 200 || host != "d.org" {
		t.Fatalf("code=%d err=%v host=%q", code, err, host)
	}
}

func TestTransportBase(t *testing.T) {
	if _, base := Transport(&config.Config{TLSMode: "off"}, "odk.d.org"); base != "http://odk.d.org" {
		t.Fatal(base)
	}
	if _, base := Transport(&config.Config{TLSMode: "files"}, "odk.d.org"); base != "https://odk.d.org" {
		t.Fatal(base)
	}
}
