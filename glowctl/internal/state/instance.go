package state

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// InstanceFile in the state dir names the compose project that owns it.
const InstanceFile = ".glow-instance"

// owner is the project recorded for stateDir: the instance file, else "glow"
// for an install that predates the file (deployment.json exists), else "".
func owner(stateDir string) (string, error) {
	b, err := os.ReadFile(filepath.Join(stateDir, InstanceFile))
	if err == nil {
		return strings.TrimSpace(string(b)), nil
	}
	if !os.IsNotExist(err) {
		return "", err
	}
	if _, err := os.Stat(filepath.Join(stateDir, "deployment.json")); err == nil {
		return "glow", nil
	}
	return "", nil
}

// CheckInstance refuses a state dir that belongs to another compose project.
func CheckInstance(stateDir, project string) error {
	o, err := owner(stateDir)
	if err != nil {
		return err
	}
	if o != "" && o != project {
		return fmt.Errorf("this state dir (%s) belongs to project %s; GLOW_INSTANCE cannot change after install (this glow.env gives project %s)", stateDir, o, project)
	}
	return nil
}

// ClaimInstance checks, then records project as the owner if not yet recorded.
func ClaimInstance(stateDir, project string) error {
	if err := CheckInstance(stateDir, project); err != nil {
		return err
	}
	p := filepath.Join(stateDir, InstanceFile)
	if _, err := os.Stat(p); os.IsNotExist(err) {
		return WriteFileAtomic(p, []byte(project+"\n"), 0o644)
	}
	return nil
}
