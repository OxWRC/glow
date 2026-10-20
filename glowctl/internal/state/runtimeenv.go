package state

import (
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/oxwrc/glow/glowctl/internal/config"
)

// ReleaseVersion returns the release dir's VERSION file (trimmed), or "dev".
func ReleaseVersion(releaseDir string) string {
	b, err := os.ReadFile(filepath.Join(releaseDir, "VERSION"))
	if v := strings.TrimSpace(string(b)); err == nil && v != "" {
		return v
	}
	return "dev"
}

// envQuote single-quotes values compose's env-file parser would mangle.
func envQuote(v string) (string, error) {
	if strings.ContainsAny(v, "\n\r'") {
		return "", fmt.Errorf("value contains a newline or single quote and cannot be written to runtime.env")
	}
	if strings.ContainsAny(v, " \t#$\"\\`") {
		return "'" + v + "'", nil
	}
	return v, nil
}

// RuntimeEnv builds the runtime.env content as ordered KEY=VALUE pairs.
func RuntimeEnv(cfg *config.Config, s Secrets, releaseDir string) ([][2]string, error) {
	d := cfg.Domain
	version := cfg.Version
	if version == "" {
		version = ReleaseVersion(releaseDir)
	}
	certDir := cfg.TLSCertDir
	if cfg.TLSMode != "files" || certDir == "" {
		certDir = filepath.Join(cfg.StateDir, "certs")
	}
	odkEmail := "glow-api@" + d
	adminEmail := "glow-admin@" + d
	if cfg.Demo {
		odkEmail, adminEmail = "api@glow.local", "admin@glow.local"
	}
	kv := [][2]string{
		{"GLOW_DOMAIN", d},
		{"GLOW_VERSION", version},
		{"APP_VERSION", version},
		{"GLOW_STATE_DIR", cfg.StateDir},
		{"GLOW_VINTAGES_DIR", "/var/lib/glow-vintages"},
		{"GLOW_DEMO_MODE", strconv.FormatBool(cfg.Demo)},
		{"GLOW_TLS_MODE", cfg.TLSMode},
		{"GLOW_TLS_CERT_DIR", certDir},
		{"GLOW_HTTP_PORT", strconv.Itoa(cfg.HTTPPort)},
		{"GLOW_HTTPS_PORT", strconv.Itoa(cfg.HTTPSPort)},
		{"GLOW_ACME_EMAIL", cfg.ACMEEmail},
		{"GLOW_PERIOD_TIMEZONE", cfg.PeriodTimezone},
		{"GLOW_OIDC_ISSUER", cfg.OIDCIssuer},
		{"GLOW_OIDC_CLIENT_ID", cfg.OIDCClientID},
		{"GLOW_SECRET_KEY", s.SecretKey},
		{"GLOW_MIN_N", "5"},
		{"GLOW_ODK_API_EMAIL", odkEmail},
		{"GLOW_ODK_API_PASSWORD", s.ODKAPIPassword},
		{"GLOW_ODK_PROJECT_ID", "1"},
		{"GLOW_ODK_FORM_ID", "bewell_questionnaire"},
		{"GLOW_DATA_CACHE_PATH", ".cache.parquet"},
		{"GLOW_DATA_REFRESH_HOURS", "1"},
		{"GLOW_CORS_ORIGINS", `["https://` + d + `"]`},
		{"PUBLIC_API_BASE", "https://api." + d},
		{"POSTGRES_DB", "glow"},
		{"POSTGRES_USER", "glow"},
		{"POSTGRES_PASSWORD", s.PostgresPassword},
		{"ODK_DOMAIN", "odk." + d},
		{"ODK_SYSADMIN_EMAIL", adminEmail},
		{"ODK_SSL_TYPE", "upstream"},
		{"ODK_POSTGRES_DB", "odk"},
		{"ODK_POSTGRES_USER", "odk"},
		{"ODK_POSTGRES_PASSWORD", s.ODKPostgresPass},
		{"ODK_PUBLIC_HTTPS_PORT", "443"},
		{"GLOW_SMTP_HOST", cfg.SMTP.Host},
		{"GLOW_SMTP_PORT", strconv.Itoa(cfg.SMTP.Port)},
		{"GLOW_SMTP_SECURE", strconv.FormatBool(cfg.SMTP.Secure)},
		{"GLOW_SMTP_USER", cfg.SMTP.User},
		{"GLOW_SMTP_PASSWORD", cfg.SMTP.Password},
	}
	if cfg.SMTP.From != "" {
		kv = append(kv, [2]string{"GLOW_SMTP_FROM", cfg.SMTP.From})
	}
	if cfg.Registry != "" {
		kv = append(kv, [2]string{"GLOW_REGISTRY", cfg.Registry})
	}
	if !cfg.Demo {
		kv = append(kv,
			[2]string{"GLOW_ODK_API_URL", "http://nginx"},
			[2]string{"GLOW_ODK_HOST_HEADER", "odk." + d},
			[2]string{"GLOW_ODK_VERIFY_SSL", "false"})
	}
	return kv, nil
}

// WriteRuntimeEnv writes runtime.env (0600, atomic). releaseDir supplies VERSION.
func WriteRuntimeEnv(cfg *config.Config, s Secrets, releaseDir, path string) error {
	kv, err := RuntimeEnv(cfg, s, releaseDir)
	if err != nil {
		return err
	}
	var sb strings.Builder
	for _, p := range kv {
		q := p[1]
		if p[0] != "GLOW_CORS_ORIGINS" {
			if q, err = envQuote(p[1]); err != nil {
				return fmt.Errorf("%s: %v", p[0], err)
			}
		}
		sb.WriteString(p[0] + "=" + q + "\n")
	}
	return WriteFileAtomic(path, []byte(sb.String()), 0o600)
}
