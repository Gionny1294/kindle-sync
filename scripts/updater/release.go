package main

import (
	"archive/tar"
	"compress/gzip"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"net/http"
	"net/url"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"
)

const (
	releaseURL   = "https://api.github.com/repositories/1459117700/releases/latest"
	archiveName  = "kindle-sync-local.tar.gz"
	maxArchive   = 96 * 1024 * 1024
	maxExtracted = 192 * 1024 * 1024
)

var versionPattern = regexp.MustCompile(`^(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})$`)

func newer(candidate, current string) bool {
	if !versionPattern.MatchString(candidate) || !versionPattern.MatchString(current) {
		return false
	}
	parts := strings.Split(candidate, ".")
	for i, part := range strings.Split(current, ".") {
		left, _ := strconv.Atoi(parts[i])
		right, _ := strconv.Atoi(part)
		if left != right {
			return left > right
		}
	}
	return false
}

type releaseAsset struct {
	Name   string
	URL    string `json:"browser_download_url"`
	Digest string
	Size   int64
}

type release struct {
	Tag        string `json:"tag_name"`
	Draft      bool
	Prerelease bool
	Assets     []releaseAsset
}

type releaseClient struct {
	client   *http.Client
	endpoint string
}

func githubClient() releaseClient {
	return releaseClient{
		endpoint: releaseURL,
		client: &http.Client{
			Timeout: 3 * time.Minute,
			CheckRedirect: func(request *http.Request, via []*http.Request) error {
				hosts := map[string]bool{"api.github.com": true, "github.com": true, "release-assets.githubusercontent.com": true, "objects.githubusercontent.com": true}
				if len(via) >= 5 || request.URL.Scheme != "https" || request.URL.User != nil || !hosts[request.URL.Host] {
					return errors.New("unexpected release redirect")
				}
				return nil
			},
		},
	}
}

func (api releaseClient) open(address string) (io.ReadCloser, error) {
	request, err := http.NewRequest(http.MethodGet, address, nil)
	if err != nil {
		return nil, err
	}
	request.Header.Set("User-Agent", "Kindle-Sync-Updater")
	response, err := api.client.Do(request)
	if err != nil {
		return nil, err
	}
	if response.StatusCode != http.StatusOK {
		response.Body.Close()
		return nil, fmt.Errorf("release HTTP %d", response.StatusCode)
	}
	return response.Body, nil
}

func (api releaseClient) latest(current string) (string, releaseAsset, error) {
	body, err := api.open(api.endpoint)
	if err != nil {
		return "", releaseAsset{}, err
	}
	defer body.Close()
	var info release
	if err := json.NewDecoder(io.LimitReader(body, 2*1024*1024)).Decode(&info); err != nil {
		return "", releaseAsset{}, err
	}
	version, hasPrefix := strings.CutPrefix(info.Tag, "v")
	if !hasPrefix || info.Draft || info.Prerelease || !versionPattern.MatchString(version) {
		return "", releaseAsset{}, errors.New("invalid stable release tag")
	}
	if !newer(version, current) {
		return "", releaseAsset{}, nil
	}
	for _, asset := range info.Assets {
		if asset.Name != archiveName {
			continue
		}
		address, err := url.Parse(asset.URL)
		digest, hasDigest := strings.CutPrefix(asset.Digest, "sha256:")
		decoded, digestErr := hex.DecodeString(digest)
		if err != nil || address.Scheme != "https" || address.Host != "github.com" || address.User != nil ||
			!strings.HasSuffix(address.Path, "/kindle-sync/releases/download/"+info.Tag+"/"+archiveName) ||
			!hasDigest || digestErr != nil || len(decoded) != sha256.Size || asset.Size <= 0 || asset.Size > maxArchive {
			return "", releaseAsset{}, errors.New("invalid release asset metadata")
		}
		return version, asset, nil
	}
	return "", releaseAsset{}, errors.New("release archive missing")
}

func (api releaseClient) download(asset releaseAsset, destination string) error {
	body, err := api.open(asset.URL)
	if err != nil {
		return err
	}
	defer body.Close()
	file, err := os.OpenFile(destination, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}
	digest := sha256.New()
	size, writeErr := io.Copy(io.MultiWriter(file, digest), io.LimitReader(body, asset.Size+1))
	closeErr := file.Close()
	if writeErr != nil || closeErr != nil {
		return errors.Join(writeErr, closeErr)
	}
	if size != asset.Size || "sha256:"+hex.EncodeToString(digest.Sum(nil)) != strings.ToLower(asset.Digest) {
		return errors.New("release archive verification failed")
	}
	return nil
}

var rootFiles = map[string]bool{
	".dockerignore": true, ".env.example": true, ".gitignore": true, "Dockerfile": true,
	"README.md": true, "INSTALL.md": true, "VERSION": true, "Start.cmd": true, "Start.command": true,
	"docker-compose.yml": true, "kindle-sync.py": true, "requirements.txt": true,
	"scripts/bootstrap.py": true, "scripts/start.sh": true, "scripts/start.ps1": true,
}

func releasePath(name string) bool {
	if !fs.ValidPath(name) || name == "." || strings.ContainsAny(name, "\\:*?\"<>|\x00\r\n\t") {
		return false
	}
	if rootFiles[name] {
		return true
	}
	for _, piece := range strings.Split(name, "/") {
		// Windows interprets these names as devices and drops trailing dots and spaces.
		base := strings.ToUpper(strings.SplitN(piece, ".", 2)[0])
		if strings.HasPrefix(piece, ".") || strings.HasSuffix(piece, ".") || strings.HasSuffix(piece, " ") ||
			base == "CON" || base == "PRN" || base == "AUX" || base == "NUL" ||
			(len(base) == 4 && (strings.HasPrefix(base, "COM") || strings.HasPrefix(base, "LPT"))) {
			return false
		}
	}
	return strings.HasPrefix(name, "app/") || strings.HasPrefix(name, "scripts/updater/")
}

func unpack(archivePath, destination, version string) error {
	archive, err := os.Open(archivePath)
	if err != nil {
		return err
	}
	defer archive.Close()
	stream, err := gzip.NewReader(archive)
	if err != nil {
		return err
	}
	defer stream.Close()
	reader := tar.NewReader(stream)
	seen := make(map[string]bool)
	var total int64
	for {
		entry, err := reader.Next()
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			return err
		}
		name, prefixed := strings.CutPrefix(entry.Name, "kindle-sync/")
		key := strings.ToLower(name)
		if !prefixed || !releasePath(name) || path.Clean(name) != name || seen[key] ||
			entry.Typeflag != tar.TypeReg || entry.Size < 0 || entry.Size > maxExtracted-total || len(seen) >= 2048 {
			return errors.New("unexpected archive entry")
		}
		total += entry.Size
		seen[key] = true
		target := filepath.Join(destination, filepath.FromSlash(name))
		if err := os.MkdirAll(filepath.Dir(target), 0755); err != nil {
			return err
		}
		mode := os.FileMode(0644)
		if entry.Mode&0111 != 0 {
			mode = 0755
		}
		file, err := os.OpenFile(target, os.O_WRONLY|os.O_CREATE|os.O_EXCL, mode)
		if err != nil {
			return err
		}
		_, copyErr := io.Copy(file, reader)
		if err := errors.Join(copyErr, file.Chmod(mode), file.Close()); err != nil {
			return err
		}
	}
	for _, name := range []string{"version", "dockerfile", "docker-compose.yml", "requirements.txt", "app/web.py", "app/worker.py"} {
		if !seen[name] {
			return fmt.Errorf("archive missing %s", name)
		}
	}
	actual, err := readVersion(destination)
	if err != nil || actual != version {
		return errors.New("archive version does not match its release")
	}
	// Compose copies these permissions into the image, whose app user is not root.
	return filepath.WalkDir(destination, func(name string, entry fs.DirEntry, err error) error {
		if err == nil && entry.IsDir() {
			return os.Chmod(name, 0755)
		}
		return err
	})
}
