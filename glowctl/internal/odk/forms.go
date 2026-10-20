package odk

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"encoding/xml"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"sort"

	"github.com/oxwrc/glow/glowctl/internal/state"
)

type formState map[string]struct {
	Hash string `json:"hash"`
}

// hashForm is sha256 of content with trailing newlines trimmed (compatible
// with the state files written by the old activation script).
func hashForm(b []byte) string {
	s := sha256.Sum256(bytes.TrimRight(b, "\r\n"))
	return hex.EncodeToString(s[:])
}

// formIDVersion reads id and version from the first child element of the
// model's primary <instance> (the first <instance> in the document).
func formIDVersion(b []byte) (id, version string, err error) {
	d := xml.NewDecoder(bytes.NewReader(b))
	inInstance := false
	for {
		tok, err := d.Token()
		if err == io.EOF {
			return "", "", errors.New("no <instance> data element found")
		}
		if err != nil {
			return "", "", err
		}
		se, ok := tok.(xml.StartElement)
		if !ok {
			continue
		}
		if !inInstance {
			inInstance = se.Name.Local == "instance"
			continue
		}
		for _, a := range se.Attr {
			switch a.Name.Local {
			case "id":
				id = a.Value
			case "version":
				version = a.Value
			}
		}
		if id == "" {
			return "", "", errors.New("form data element has no id attribute")
		}
		return id, version, nil
	}
}

func (c *Client) postXML(ctx context.Context, path, token string, body []byte, fallback bool) (int, error) {
	req, err := http.NewRequestWithContext(ctx, "POST", c.base+path, bytes.NewReader(body))
	if err != nil {
		return 0, err
	}
	req.Host = c.host
	req.Header.Set("Content-Type", "application/xml")
	if fallback {
		req.Header.Set("X-XmlFormId-Fallback", "true")
	}
	req.Header.Set("Authorization", "Bearer "+token)
	resp, err := c.hc.Do(req)
	if err != nil {
		return 0, fmt.Errorf("POST %s: %w", path, err)
	}
	defer resp.Body.Close()
	io.Copy(io.Discard, io.LimitReader(resp.Body, 1<<20))
	return resp.StatusCode, nil
}

// publishForm uploads one form; changed=false means ODK already has this version.
// On error an orphan draft may be left behind in ODK (harmless; the next
// draft upload replaces it).
func (c *Client) publishForm(ctx context.Context, token string, project int, id, version string, body []byte) (changed bool, err error) {
	base := fmt.Sprintf("/v1/projects/%d/forms", project)
	code, err := c.postXML(ctx, base+"?publish=true", token, body, true)
	if err != nil {
		return false, err
	}
	if code/100 == 2 {
		return true, nil
	}
	if code != http.StatusConflict {
		return false, statusErr("POST", base, code)
	}
	// Form exists. Skip when the published version already matches.
	formPath := base + "/" + url.PathEscape(id)
	var cur struct {
		Version string `json:"version"`
	}
	if err := c.getJSON(ctx, formPath, token, &cur); err != nil {
		return false, err
	}
	if cur.Version == version {
		return false, nil
	}
	draft := formPath + "/draft"
	if code, err = c.postXML(ctx, draft+"?ignoreWarnings=true", token, body, false); err != nil {
		return false, err
	} else if code/100 != 2 {
		return false, statusErr("POST", draft, code)
	}
	pub := draft + "/publish"
	code, _, err = c.do(ctx, "POST", pub, token, nil)
	if err != nil {
		return false, err
	}
	switch {
	case code/100 == 2:
		return true, nil
	case code == http.StatusConflict:
		// 409.6: version already published (raced or equal) - unchanged.
		return false, nil
	}
	return false, statusErr("POST", pub, code)
}

// SyncForms uploads/publishes every *.xml in formsDir (sorted by filename)
// whose content hash differs from statePath, saving state after each form.
// It returns the xmlFormIds actually uploaded (repeated if several files
// share an id). Files sharing an id end with the later file's hash.
func SyncForms(ctx context.Context, c *Client, token string, projectID int, formsDir, statePath string) (uploaded []string, err error) {
	st := formState{}
	if b, err := os.ReadFile(statePath); err == nil {
		if err := json.Unmarshal(b, &st); err != nil {
			return nil, fmt.Errorf("parse %s: %w", statePath, err)
		}
	} else if !errors.Is(err, fs.ErrNotExist) {
		return nil, err
	}
	names, err := filepath.Glob(filepath.Join(formsDir, "*.xml"))
	if err != nil {
		return nil, err
	}
	sort.Strings(names)
	for _, p := range names {
		body, err := os.ReadFile(p)
		if err != nil {
			return uploaded, err
		}
		id, version, err := formIDVersion(body)
		if err != nil {
			return uploaded, fmt.Errorf("%s: %w", filepath.Base(p), err)
		}
		h := hashForm(body)
		if st[id].Hash == h {
			continue
		}
		changed, err := c.publishForm(ctx, token, projectID, id, version, body)
		if err != nil {
			return uploaded, fmt.Errorf("form %s (%s): %w", id, filepath.Base(p), err)
		}
		if changed {
			uploaded = append(uploaded, id)
		}
		st[id] = struct {
			Hash string `json:"hash"`
		}{h}
		out, err := json.MarshalIndent(st, "", "  ")
		if err != nil {
			return uploaded, err
		}
		if err := state.WriteFileAtomic(statePath, append(out, '\n'), 0o600); err != nil {
			return uploaded, err
		}
	}
	return uploaded, nil
}
