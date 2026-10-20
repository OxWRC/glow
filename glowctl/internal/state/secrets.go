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
	BackupKey        string
}

func (s *Secrets) fields() map[string]*string {
	return map[string]*string{
		"GLOW_SECRET_KEY":       &s.SecretKey,
		"POSTGRES_PASSWORD":     &s.PostgresPassword,
		"ODK_POSTGRES_PASSWORD": &s.ODKPostgresPass,
		"ODK_ADMIN_PASSWORD":    &s.ODKAdminPassword,
		"ODK_API_PASSWORD":      &s.ODKAPIPassword,
		"GLOW_BACKUP_KEY":       &s.BackupKey,
	}
}

// requiredKeys existed from the first release: a secrets.env missing one is
// damaged, and regenerating it would lock the stack out of its own data.
var requiredKeys = []string{"GLOW_SECRET_KEY", "POSTGRES_PASSWORD", "ODK_POSTGRES_PASSWORD", "ODK_ADMIN_PASSWORD", "ODK_API_PASSWORD"}

// laterKeys were added in later releases: generated when missing so older
// installs gain them, but an existing value is never replaced.
var laterKeys = []string{"GLOW_BACKUP_KEY"}

func randomHex() (string, error) {
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return hex.EncodeToString(b), nil
}

// EnsureSecrets returns <stateDir>/secrets.env, creating it (0600, atomic)
// when absent, and the keys it generated in this call. A missing required key
// is an error; missing later additions are generated and appended (atomic
// rewrite keeping the existing lines). No existing secret is ever regenerated.
func EnsureSecrets(stateDir string) (Secrets, []string, error) {
	var s Secrets
	path := filepath.Join(stateDir, "secrets.env")
	data, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		if err := os.MkdirAll(stateDir, 0o755); err != nil {
			return s, nil, err
		}
		data, err = nil, nil
	} else if err != nil {
		return s, nil, err
	} else {
		f := s.fields()
		for _, line := range strings.Split(string(data), "\n") {
			k, v, ok := strings.Cut(strings.TrimSpace(line), "=")
			if dst, known := f[k]; ok && known {
				*dst = v
			}
		}
		for _, k := range requiredKeys {
			if *f[k] == "" {
				return Secrets{}, nil, fmt.Errorf("%s is missing %s; restore it from backup (secrets are never regenerated)", path, k)
			}
		}
	}
	var created []string
	add := string(data)
	if add != "" && !strings.HasSuffix(add, "\n") {
		add += "\n"
	}
	f := s.fields()
	for _, k := range append(append([]string{}, requiredKeys...), laterKeys...) {
		if *f[k] != "" {
			continue
		}
		v, err := randomHex()
		if err != nil {
			return Secrets{}, nil, err
		}
		*f[k] = v
		add += k + "=" + v + "\n"
		created = append(created, k)
	}
	if len(created) == 0 {
		return s, nil, nil
	}
	if err := WriteFileAtomic(path, []byte(add), 0o600); err != nil {
		return Secrets{}, nil, err
	}
	return s, created, nil
}
