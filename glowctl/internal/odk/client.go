// Package odk talks to ODK Central through the local proxy.
package odk

import (
	"bytes"
	"context"
	"crypto/tls"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"

	"github.com/oxwrc/glow/glowctl/internal/config"
)

// Client is a minimal ODK Central API client. Requests carry Host header host.
type Client struct {
	base string
	host string
	hc   *http.Client
}

// NewClient dials the local Caddy proxy (loopback) with SNI/Host odk.<domain>,
// or plain HTTP to the proxy's HTTP port in off mode.
func NewClient(cfg *config.Config) *Client {
	host := "odk." + cfg.Domain
	d := &net.Dialer{Timeout: 10 * time.Second}
	tr := &http.Transport{}
	var base string
	if cfg.TLSMode == "off" {
		base = "http://" + host
		addr := net.JoinHostPort("127.0.0.1", strconv.Itoa(cfg.HTTPPort))
		tr.DialContext = func(ctx context.Context, n, _ string) (net.Conn, error) { return d.DialContext(ctx, n, addr) }
	} else {
		base = "https://" + host
		addr := net.JoinHostPort("127.0.0.1", strconv.Itoa(cfg.HTTPSPort))
		tr.DialContext = func(ctx context.Context, n, _ string) (net.Conn, error) { return d.DialContext(ctx, n, addr) }
		// Loopback to our own proxy; ACME may not have issued a cert yet.
		tr.TLSClientConfig = &tls.Config{ServerName: host, InsecureSkipVerify: true}
	}
	return &Client{base: base, host: host, hc: &http.Client{Timeout: 30 * time.Second, Transport: tr}}
}

// NewClientForBase targets baseURL directly (tests), sending Host: host.
func NewClientForBase(baseURL, host string) *Client {
	return &Client{base: baseURL, host: host, hc: &http.Client{Timeout: 30 * time.Second}}
}

// do sends a request and returns status and body. token may be empty.
func (c *Client) do(ctx context.Context, method, path, token string, body any) (int, []byte, error) {
	var rd io.Reader
	if body != nil {
		b, err := json.Marshal(body)
		if err != nil {
			return 0, nil, err
		}
		rd = bytes.NewReader(b)
	}
	req, err := http.NewRequestWithContext(ctx, method, c.base+path, rd)
	if err != nil {
		return 0, nil, err
	}
	req.Host = c.host
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	resp, err := c.hc.Do(req)
	if err != nil {
		return 0, nil, fmt.Errorf("%s %s: %w", method, path, err)
	}
	defer resp.Body.Close()
	b, err := io.ReadAll(io.LimitReader(resp.Body, 16<<20))
	return resp.StatusCode, b, err
}

func statusErr(method, path string, code int) error {
	return fmt.Errorf("ODK %s %s: unexpected HTTP status %d", method, path, code)
}

func (c *Client) getJSON(ctx context.Context, path, token string, out any) error {
	code, b, err := c.do(ctx, "GET", path, token, nil)
	if err != nil {
		return err
	}
	if code/100 != 2 {
		return statusErr("GET", path, code)
	}
	return json.Unmarshal(b, out)
}

// Ready reports whether GET / answers with a non-error status.
func (c *Client) Ready(ctx context.Context) error {
	code, _, err := c.do(ctx, "GET", "/", "", nil)
	if err != nil {
		return err
	}
	if code < 200 || code >= 400 {
		return statusErr("GET", "/", code)
	}
	return nil
}

// WaitReady polls Ready up to attempts times, interval apart.
func WaitReady(ctx context.Context, c *Client, attempts int, interval time.Duration) error {
	var last error
	for i := 0; i < attempts; i++ {
		if last = c.Ready(ctx); last == nil {
			return nil
		}
		if i == attempts-1 {
			break
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(interval):
		}
	}
	return fmt.Errorf("ODK not ready after %d attempts: %w", attempts, last)
}

// Login returns a session token.
func (c *Client) Login(ctx context.Context, email, password string) (string, error) {
	code, b, err := c.do(ctx, "POST", "/v1/sessions", "", map[string]string{"email": email, "password": password})
	if err != nil {
		return "", err
	}
	if code/100 != 2 {
		return "", fmt.Errorf("ODK login as %s failed: HTTP %d", email, code)
	}
	var r struct {
		Token string `json:"token"`
	}
	if err := json.Unmarshal(b, &r); err != nil || r.Token == "" {
		return "", fmt.Errorf("ODK login as %s: no token in response", email)
	}
	return r.Token, nil
}

// Logout deletes the session.
func (c *Client) Logout(ctx context.Context, token string) error {
	path := "/v1/sessions/" + url.PathEscape(token)
	code, _, err := c.do(ctx, "DELETE", path, token, nil)
	if err != nil {
		// The URL (and error text) contains the token; never surface it.
		return fmt.Errorf("ODK logout: request failed")
	}
	if code/100 != 2 {
		return fmt.Errorf("ODK logout: HTTP %d", code)
	}
	return nil
}

// ProjectByName returns the id of the project with exactly this name, or 0.
func (c *Client) ProjectByName(ctx context.Context, token, name string) (int, error) {
	var ps []struct {
		ID   int    `json:"id"`
		Name string `json:"name"`
	}
	if err := c.getJSON(ctx, "/v1/projects", token, &ps); err != nil {
		return 0, err
	}
	for _, p := range ps {
		if p.Name == name {
			return p.ID, nil
		}
	}
	return 0, nil
}

// CreateProject creates a project and returns its id.
func (c *Client) CreateProject(ctx context.Context, token, name string) (int, error) {
	code, b, err := c.do(ctx, "POST", "/v1/projects", token, map[string]string{"name": name})
	if err != nil {
		return 0, err
	}
	if code/100 != 2 {
		return 0, statusErr("POST", "/v1/projects", code)
	}
	var r struct {
		ID int `json:"id"`
	}
	if err := json.Unmarshal(b, &r); err != nil || r.ID == 0 {
		return 0, fmt.Errorf("ODK create project: no id in response")
	}
	return r.ID, nil
}

// UserByEmail returns the actor id of the user, or 0 when absent.
func (c *Client) UserByEmail(ctx context.Context, token, email string) (int, error) {
	var us []struct {
		ID    int    `json:"id"`
		Email string `json:"email"`
	}
	if err := c.getJSON(ctx, "/v1/users", token, &us); err != nil {
		return 0, err
	}
	for _, u := range us {
		if strings.EqualFold(u.Email, email) {
			return u.ID, nil
		}
	}
	return 0, nil
}

// CreateUser creates a web user and returns its id.
func (c *Client) CreateUser(ctx context.Context, token, email, password string) (int, error) {
	code, b, err := c.do(ctx, "POST", "/v1/users", token, map[string]string{"email": email, "password": password})
	if err != nil {
		return 0, err
	}
	if code/100 != 2 {
		return 0, fmt.Errorf("ODK create user %s: HTTP %d", email, code)
	}
	var r struct {
		ID int `json:"id"`
	}
	if err := json.Unmarshal(b, &r); err != nil || r.ID == 0 {
		return 0, fmt.Errorf("ODK create user %s: no id in response", email)
	}
	return r.ID, nil
}

// ViewerRole is ODK's Project Viewer role id.
const ViewerRole = 2

// HasAssignment reports whether actor holds roleID on the project.
func (c *Client) HasAssignment(ctx context.Context, token string, project, roleID, actor int) (bool, error) {
	var as []struct {
		ActorID int `json:"actorId"`
		RoleID  int `json:"roleId"`
	}
	if err := c.getJSON(ctx, fmt.Sprintf("/v1/projects/%d/assignments", project), token, &as); err != nil {
		return false, err
	}
	for _, a := range as {
		if a.ActorID == actor && a.RoleID == roleID {
			return true, nil
		}
	}
	return false, nil
}

// Assign grants roleID to actor on the project. 409 (already assigned) is success.
func (c *Client) Assign(ctx context.Context, token string, project, roleID, actor int) error {
	path := fmt.Sprintf("/v1/projects/%d/assignments/%d/%d", project, roleID, actor)
	code, _, err := c.do(ctx, "POST", path, token, nil)
	if err != nil {
		return err
	}
	if code/100 != 2 && code != http.StatusConflict {
		return statusErr("POST", path, code)
	}
	return nil
}
