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
	"strings"

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

func (c *Client) postXML(ctx context.Context, path, token string, body []byte, fallback bool) (int, []byte, error) {
	req, err := http.NewRequestWithContext(ctx, "POST", c.base+path, bytes.NewReader(body))
	if err != nil {
		return 0, nil, err
	}
	req.Host = c.host
	req.Header.Set("Content-Type", "application/xml")
	if fallback {
		req.Header.Set("X-XmlFormId-Fallback", "true")
	}
	req.Header.Set("Authorization", "Bearer "+token)
	resp, err := c.hc.Do(req)
	if err != nil {
		return 0, nil, fmt.Errorf("POST %s: %w", path, err)
	}
	defer resp.Body.Close()
	b, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	return resp.StatusCode, b, nil
}

// formErr is a status error that includes ODK's (truncated) response body.
func formErr(method, path string, code int, body []byte) error {
	m := strings.Join(strings.Fields(string(body)), " ")
	if len(m) > 200 {
		m = m[:200] + "..."
	}
	if m == "" {
		return statusErr(method, path, code)
	}
	return fmt.Errorf("%w: %s", statusErr(method, path, code), m)
}

// publishForm uploads one form; changed=false means ODK already has this version.
// On error an orphan draft may be left behind in ODK (harmless; the next
// draft upload replaces it).
func (c *Client) publishForm(ctx context.Context, token string, project int, id, version string, body []byte) (changed bool, err error) {
	base := fmt.Sprintf("/v1/projects/%d/forms", project)
	code, rb, err := c.postXML(ctx, base+"?publish=true", token, body, true)
	if err != nil {
		return false, err
	}
	if code/100 == 2 {
		return true, nil
	}
	if code != http.StatusConflict {
		return false, formErr("POST", base, code, rb)
	}
	// Form exists. Skip when the published version already matches.
	formPath := base + "/" + url.PathEscape(id)
	code, rb, err = c.do(ctx, "GET", formPath, token, nil)
	if err != nil {
		return false, err
	}
	if code/100 != 2 {
		return false, formErr("GET", formPath, code, rb)
	}
	var cur struct {
		Version string `json:"version"`
	}
	if err := json.Unmarshal(rb, &cur); err != nil {
		return false, err
	}
	if cur.Version == version {
		return false, nil
	}
	draft := formPath + "/draft"
	if code, rb, err = c.postXML(ctx, draft+"?ignoreWarnings=true", token, body, false); err != nil {
		return false, err
	} else if code/100 != 2 {
		return false, formErr("POST", draft, code, rb)
	}
	pub := draft + "/publish"
	code, rb, err = c.do(ctx, "POST", pub, token, nil)
	if err != nil {
		return false, err
	}
	switch {
	case code/100 == 2:
		return true, nil
	case code == http.StatusConflict:
		// 409.6: version already published. Remove our orphan draft.
		dc, db, err := c.do(ctx, "DELETE", draft, token, nil)
		if err != nil {
			return false, err
		}
		if dc/100 != 2 {
			return false, formErr("DELETE", draft, dc, db)
		}
		return false, nil
	}
	return false, formErr("POST", pub, code, rb)
}

// SyncForms uploads/publishes every *.xml in formsDir whose content hash
// differs from statePath, saving state after each form. Files are ordered by
// filename and, when several share an xmlFormId (e.g. bewell v1/v2), only the
// last one is synced (older ones are skipped with a note), so reruns are
// idempotent. It returns the xmlFormIds actually uploaded.
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
	type form struct {
		name, id, version string
		body              []byte
	}
	var forms []form
	last := map[string]int{}
	for _, p := range names {
		body, err := os.ReadFile(p)
		if err != nil {
			return nil, err
		}
		id, version, err := formIDVersion(body)
		if err != nil {
			return nil, fmt.Errorf("%s: %w", filepath.Base(p), err)
		}
		if version == "" {
			return nil, fmt.Errorf("%s: form has no version attribute; ODK needs a unique version for updates", filepath.Base(p))
		}
		if i, ok := last[id]; ok {
			fmt.Printf("[glowctl] skipping %s: superseded by %s (same xmlFormId %s)\n", forms[i].name, filepath.Base(p), id)
			forms[i].body = nil
		}
		last[id] = len(forms)
		forms = append(forms, form{filepath.Base(p), id, version, body})
	}
	for _, f := range forms {
		if f.body == nil {
			continue
		}
		h := hashForm(f.body)
		if st[f.id].Hash == h {
			continue
		}
		changed, err := c.publishForm(ctx, token, projectID, f.id, f.version, f.body)
		if err != nil {
			return uploaded, fmt.Errorf("form %s (%s): %w", f.id, f.name, err)
		}
		if changed {
			uploaded = append(uploaded, f.id)
		}
		st[f.id] = struct {
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
