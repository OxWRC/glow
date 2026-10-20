// Package proxy reaches the stack through the local Caddy proxy on loopback.
package proxy

import (
	"context"
	"crypto/tls"
	"net"
	"net/http"
	"strconv"
	"time"

	"github.com/oxwrc/glow/glowctl/internal/config"
)

// Targets are the proxy checks: dashboard, api (index 1) and ODK.
func Targets(cfg *config.Config) [][2]string {
	return [][2]string{{cfg.Domain, "/en/login"}, {"api." + cfg.Domain, "/health"}, {"odk." + cfg.Domain, "/"}}
}

// Transport dials the proxy on loopback with SNI = host (plain HTTP to the
// HTTP port in off mode) and returns it with the base URL for host.
// Certificates are not checked: it is our own proxy and ACME may not have issued.
func Transport(cfg *config.Config, host string) (*http.Transport, string) {
	scheme, port := "https", cfg.HTTPSPort
	if cfg.TLSMode == "off" {
		scheme, port = "http", cfg.HTTPPort
	}
	addr := net.JoinHostPort("127.0.0.1", strconv.Itoa(port))
	d := &net.Dialer{Timeout: 10 * time.Second}
	tr := &http.Transport{
		DialContext: func(ctx context.Context, n, _ string) (net.Conn, error) { return d.DialContext(ctx, n, addr) },
	}
	if scheme == "https" {
		tr.TLSClientConfig = &tls.Config{ServerName: host, InsecureSkipVerify: true}
	}
	return tr, scheme + "://" + host
}

// Get fetches path on host through the proxy and returns the HTTP status.
func Get(ctx context.Context, cfg *config.Config, host, path string) (int, error) {
	tr, base := Transport(cfg, host)
	defer tr.CloseIdleConnections()
	req, err := http.NewRequestWithContext(ctx, "GET", base+path, nil)
	if err != nil {
		return 0, err
	}
	resp, err := (&http.Client{Timeout: 30 * time.Second, Transport: tr}).Do(req)
	if err != nil {
		return 0, err
	}
	resp.Body.Close()
	return resp.StatusCode, nil
}
