// Package lock serialises glowctl commands that change the deployment.
package lock

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"syscall"
)

// Acquire takes the exclusive, non-blocking flock <stateDir>/.glowctl.lock.
// The lock is released by release() or when the process exits.
func Acquire(stateDir string) (release func(), err error) {
	if err := os.MkdirAll(stateDir, 0o755); err != nil {
		return nil, err
	}
	f, err := os.OpenFile(filepath.Join(stateDir, ".glowctl.lock"), os.O_RDWR|os.O_CREATE, 0o600)
	if err != nil {
		return nil, err
	}
	if err := syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		f.Close()
		if errors.Is(err, syscall.EWOULDBLOCK) {
			return nil, errors.New("another glowctl command is running")
		}
		return nil, fmt.Errorf("locking %s: %w", f.Name(), err)
	}
	return func() { f.Close() }, nil
}
