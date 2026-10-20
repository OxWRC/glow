package state

import (
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// Secrets are generated once and never rotated by reruns.
type Secrets struct {
	SecretKey        string
	PostgresPassword string
	ODKPostgresPass  string
	ODKAdminPassword string
	ODKAPIPassword   string
}

func (s *Secrets) fields() map[string]*string {
	return map[string]*string{
		"GLOW_SECRET_KEY":       &s.SecretKey,
		"POSTGRES_PASSWORD":     &s.PostgresPassword,
		"ODK_POSTGRES_PASSWORD": &s.ODKPostgresPass,
		"ODK_ADMIN_PASSWORD":    &s.ODKAdminPassword,
		"ODK_API_PASSWORD":      &s.ODKAPIPassword,
	}
}

var secretKeys = []string{"GLOW_SECRET_KEY", "POSTGRES_PASSWORD", "ODK_POSTGRES_PASSWORD", "ODK_ADMIN_PASSWORD", "ODK_API_PASSWORD"}

func randomHex() (string, error) {
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return hex.EncodeToString(b), nil
}

// EnsureSecrets returns <stateDir>/secrets.env, creating it (0600, atomic)
// when absent. An existing file missing a key is an error, never regenerated.
func EnsureSecrets(stateDir string) (Secrets, error) {
	var s Secrets
	path := filepath.Join(stateDir, "secrets.env")
	data, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		if err := os.MkdirAll(stateDir, 0o755); err != nil {
			return s, err
		}
		var sb strings.Builder
		for _, k := range secretKeys {
			v, err := randomHex()
			if err != nil {
				return s, err
			}
			*s.fields()[k] = v
			sb.WriteString(k + "=" + v + "\n")
		}
		return s, WriteFileAtomic(path, []byte(sb.String()), 0o600)
	}
	if err != nil {
		return s, err
	}
	f := s.fields()
	for _, line := range strings.Split(string(data), "\n") {
		k, v, ok := strings.Cut(strings.TrimSpace(line), "=")
		if dst, known := f[k]; ok && known {
			*dst = v
		}
	}
	for _, k := range secretKeys {
		if *f[k] == "" {
			return Secrets{}, fmt.Errorf("%s is missing %s; restore it from backup (secrets are never regenerated)", path, k)
		}
	}
	return s, nil
}
