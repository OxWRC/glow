// Package config loads and validates the operator-facing glow.env file.
package config

import (
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"
)

// SMTPConfig holds the optional outbound mail settings.
type SMTPConfig struct {
	Host     string
	Port     int
	Secure   bool
	User     string
	Password string
	From     string
}

// Config is the parsed glow.env.
type Config struct {
	Domain         string
	OIDCIssuer     string
	OIDCClientID   string
	TLSMode        string
	TLSCertDir     string
	HTTPPort       int
	HTTPSPort      int
	Demo           bool
	StateDir       string
	BackupPath     string
	PeriodTimezone string
	ACMEEmail      string
	SMTP           SMTPConfig
	Registry       string
	Version        string

	errs []error // problems found while parsing (unknown keys, bad syntax, bad numbers)
}

var hostRe = regexp.MustCompile(`^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$`)

func unquote(v string) string {
	if len(v) >= 2 && (v[0] == '"' || v[0] == '\'') && v[len(v)-1] == v[0] {
		return v[1 : len(v)-1]
	}
	return v
}

// Load parses path. Defaults are applied; problems with the content are
// reported by Validate, so only I/O failures are returned here.
func Load(path string) (*Config, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	c := &Config{
		TLSMode: "acme", HTTPPort: 80, HTTPSPort: 443,
		StateDir: "/var/lib/glow", PeriodTimezone: "Europe/London",
		SMTP: SMTPConfig{Port: 587},
	}
	port := func(key, v string, dst *int) {
		n, err := strconv.Atoi(v)
		if err != nil || n < 1 || n > 65535 {
			c.errs = append(c.errs, fmt.Errorf("%s: %q is not a port between 1 and 65535", key, v))
			return
		}
		*dst = n
	}
	boolean := func(key, v string, dst *bool) {
		switch v {
		case "true":
			*dst = true
		case "false":
			*dst = false
		default:
			c.errs = append(c.errs, fmt.Errorf("%s: %q must be true or false", key, v))
		}
	}
	for i, line := range strings.Split(string(data), "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		k, v, ok := strings.Cut(line, "=")
		k = strings.TrimSpace(k)
		if !ok || k == "" {
			c.errs = append(c.errs, fmt.Errorf("line %d: expected KEY=VALUE", i+1))
			continue
		}
		v = unquote(strings.TrimSpace(v))
		switch k {
		case "GLOW_DOMAIN":
			c.Domain = v
		case "GLOW_OIDC_ISSUER":
			c.OIDCIssuer = v
		case "GLOW_OIDC_CLIENT_ID":
			c.OIDCClientID = v
		case "GLOW_TLS_MODE":
			c.TLSMode = v
		case "GLOW_TLS_CERT_DIR":
			c.TLSCertDir = v
		case "GLOW_HTTP_PORT":
			port(k, v, &c.HTTPPort)
		case "GLOW_HTTPS_PORT":
			port(k, v, &c.HTTPSPort)
		case "GLOW_DEMO_MODE":
			boolean(k, v, &c.Demo)
		case "GLOW_STATE_DIR":
			c.StateDir = v
		case "GLOW_BACKUP_PATH":
			c.BackupPath = v
		case "GLOW_PERIOD_TIMEZONE":
			c.PeriodTimezone = v
		case "GLOW_ACME_EMAIL":
			c.ACMEEmail = v
		case "GLOW_SMTP_HOST":
			c.SMTP.Host = v
		case "GLOW_SMTP_PORT":
			port(k, v, &c.SMTP.Port)
		case "GLOW_SMTP_SECURE":
			boolean(k, v, &c.SMTP.Secure)
		case "GLOW_SMTP_USER":
			c.SMTP.User = v
		case "GLOW_SMTP_PASSWORD":
			c.SMTP.Password = v
		case "GLOW_SMTP_FROM":
			c.SMTP.From = v
		case "GLOW_REGISTRY":
			c.Registry = v
		case "GLOW_VERSION":
			c.Version = v
		default:
			c.errs = append(c.errs, fmt.Errorf("%s: unknown key (typo?)", k))
		}
	}
	return c, nil
}

// Validate returns every problem found, each naming the offending key.
func (c *Config) Validate() []error {
	errs := append([]error(nil), c.errs...)
	add := func(format string, a ...any) { errs = append(errs, fmt.Errorf(format, a...)) }

	if c.Domain == "" {
		add("GLOW_DOMAIN: required")
	} else if !hostRe.MatchString(c.Domain) {
		add("GLOW_DOMAIN: %q is not a valid hostname", c.Domain)
	}
	if !c.Demo {
		if c.OIDCIssuer == "" {
			add("GLOW_OIDC_ISSUER: required unless GLOW_DEMO_MODE=true")
		} else if u, err := url.Parse(c.OIDCIssuer); err != nil || u.Scheme != "https" || u.Host == "" {
			add("GLOW_OIDC_ISSUER: %q must be an https:// URL", c.OIDCIssuer)
		}
		if c.OIDCClientID == "" {
			add("GLOW_OIDC_CLIENT_ID: required unless GLOW_DEMO_MODE=true")
		}
		if c.BackupPath == "" {
			add("GLOW_BACKUP_PATH: required unless GLOW_DEMO_MODE=true")
		} else if err := writableDir(c.BackupPath); err != nil {
			add("GLOW_BACKUP_PATH: %v", err)
		}
	}
	switch c.TLSMode {
	case "acme", "off":
	case "files":
		if c.TLSCertDir == "" {
			add("GLOW_TLS_CERT_DIR: required when GLOW_TLS_MODE=files")
		} else {
			for _, f := range []string{"fullchain.pem", "privkey.pem"} {
				if st, err := os.Stat(filepath.Join(c.TLSCertDir, f)); err != nil || st.IsDir() {
					add("GLOW_TLS_CERT_DIR: %s not found in %s", f, c.TLSCertDir)
				}
			}
		}
	default:
		add("GLOW_TLS_MODE: %q must be acme, files or off", c.TLSMode)
	}
	if _, err := time.LoadLocation(c.PeriodTimezone); err != nil || c.PeriodTimezone == "" {
		add("GLOW_PERIOD_TIMEZONE: %q is not an IANA timezone name", c.PeriodTimezone)
	}
	return errs
}

func writableDir(p string) error {
	st, err := os.Stat(p)
	if err != nil || !st.IsDir() {
		return fmt.Errorf("%q must be an existing directory", p)
	}
	f, err := os.CreateTemp(p, ".glowctl-write-test-*")
	if err != nil {
		return fmt.Errorf("%q is not writable", p)
	}
	f.Close()
	os.Remove(f.Name())
	return nil
}
