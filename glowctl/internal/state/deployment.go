package state

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"time"
)

// Deployment is <state>/deployment.json, written by install (and update):
// the version actually running, when it was deployed, and the mode.
type Deployment struct {
	Version    string    `json:"version"`
	DeployedAt time.Time `json:"deployed_at"`
	Demo       bool      `json:"demo"`
	// ReleaseDir is the release dir the stack runs from ("" before update
	// recorded it): update refuses to run from any other dir.
	ReleaseDir string `json:"release_dir,omitempty"`
}

// WriteDeployment writes deployment.json atomically; deployed_at is UTC "Z".
func WriteDeployment(stateDir string, d Deployment) error {
	d.DeployedAt = d.DeployedAt.UTC().Truncate(time.Second)
	b, err := json.Marshal(d)
	if err != nil {
		return err
	}
	return WriteFileAtomic(filepath.Join(stateDir, "deployment.json"), append(b, '\n'), 0o644)
}

// ReadDeployment reads deployment.json.
func ReadDeployment(stateDir string) (Deployment, error) {
	var d Deployment
	p := filepath.Join(stateDir, "deployment.json")
	b, err := os.ReadFile(p)
	if err != nil {
		return d, err
	}
	if err := json.Unmarshal(b, &d); err != nil {
		return d, fmt.Errorf("parsing %s: %w", p, err)
	}
	return d, nil
}

// RunningVersion is deployment.json's version, else the release's VERSION file.
func RunningVersion(stateDir, releaseDir string) string {
	if d, err := ReadDeployment(stateDir); err == nil && d.Version != "" {
		return d.Version
	}
	return ReleaseVersion(releaseDir)
}
