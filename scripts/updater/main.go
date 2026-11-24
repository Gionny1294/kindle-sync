package main

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

const stateDirectory = ".kindle-updater"
const requestFile = "kindle-update-request.json"
const upgradeCompose = `services:
  app:
    image: "${KINDLE_SYNC_IMAGE:?}"
    build:
      context: "${KINDLE_SYNC_SOURCE:?}"
      dockerfile: Dockerfile
  worker:
    image: "${KINDLE_SYNC_IMAGE:?}"
`

type deployment struct {
	Active  string `json:"active"`
	Pending string `json:"pending"`
}

type status struct {
	Current   string `json:"current"`
	Latest    string `json:"latest"`
	Phase     string `json:"phase"`
	Heartbeat int64  `json:"heartbeat"`
}

type request struct {
	Action  string `json:"action"`
	Version string `json:"version"`
	Issued  int64  `json:"issued"`
}

type updater struct {
	root      string
	state     string
	api       releaseClient
	runDocker func(context.Context, []string, ...string) error
}

func readVersion(directory string) (string, error) {
	data, err := os.ReadFile(filepath.Join(directory, "VERSION"))
	if err != nil {
		return "", err
	}
	version := strings.TrimSpace(string(data))
	if !versionPattern.MatchString(version) {
		return "", errors.New("invalid version")
	}
	return version, nil
}

func writeJSON(path string, value any, mode fs.FileMode) error {
	file, err := os.CreateTemp(filepath.Dir(path), ".update-*")
	if err != nil {
		return err
	}
	defer os.Remove(file.Name())
	writeErr := json.NewEncoder(file).Encode(value)
	if err := errors.Join(writeErr, file.Chmod(mode), file.Sync(), file.Close()); err != nil {
		return err
	}
	return os.Rename(file.Name(), path)
}

func (u updater) releaseDir(version string) string {
	if version == "" {
		return u.root
	}
	return filepath.Join(u.state, "releases", version)
}

func (u updater) deployPath() string {
	return filepath.Join(u.state, "deployment.json")
}

func (u updater) customCompose() bool {
	if os.Getenv("COMPOSE_FILE") != "" {
		return true
	}
	data, err := os.ReadFile(filepath.Join(u.root, ".env"))
	if err != nil {
		return false
	}
	for _, line := range strings.Split(string(data), "\n") {
		key, value, found := strings.Cut(strings.TrimPrefix(strings.TrimSpace(line), "export "), "=")
		if found && strings.TrimSpace(key) == "COMPOSE_FILE" && strings.Trim(strings.TrimSpace(value), "\"'") != "" {
			return true
		}
	}
	return false
}

func (u updater) loadDeployment() (deployment, error) {
	var state deployment
	data, err := os.ReadFile(u.deployPath())
	if errors.Is(err, fs.ErrNotExist) {
		return state, nil
	}
	if err != nil {
		return state, err
	}
	err = json.Unmarshal(data, &state)
	if err != nil || (state.Active != "" && !versionPattern.MatchString(state.Active)) ||
		(state.Pending != "" && (!versionPattern.MatchString(state.Pending) || state.Pending == state.Active)) {
		return deployment{}, errors.New("invalid local deployment record")
	}
	return state, nil
}

func (u updater) compose(version string, timeout time.Duration, args ...string) error {
	command := []string{"compose"}
	env := os.Environ()
	if version != "" {
		if u.customCompose() {
			return errors.New("custom COMPOSE_FILE requires a manual update")
		}
		source := u.releaseDir(version)
		command = append(command, "--project-directory", u.root, "-f", filepath.Join(source, "docker-compose.yml"))
		for _, name := range []string{"compose.override.yaml", "compose.override.yml", "docker-compose.override.yaml", "docker-compose.override.yml"} {
			override := filepath.Join(u.root, name)
			if _, err := os.Stat(override); err == nil {
				command = append(command, "-f", override)
				break
			}
		}
		isolateImage := filepath.Join(u.state, "upgrade.compose.yml")
		if err := os.WriteFile(isolateImage, []byte(upgradeCompose), 0600); err != nil {
			return err
		}
		command = append(command, "-f", isolateImage)
		id := sha256.Sum256([]byte(u.root))
		env = append(env, "KINDLE_SYNC_SOURCE="+source, fmt.Sprintf("KINDLE_SYNC_IMAGE=kindle-sync:update-%x-%s", id[:6], version))
	}
	context, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	return u.runDocker(context, env, append(command, args...)...)
}

func (u updater) startVersion(version string, build bool) error {
	option := "--no-build"
	if build {
		option = "--build"
	}
	return u.compose(version, 25*time.Minute, "up", "-d", option, "--wait", "--wait-timeout", "240")
}

func (u updater) recover(state deployment) (deployment, error) {
	if state.Pending != "" {
		if err := u.startVersion(state.Active, false); err != nil {
			return state, fmt.Errorf("restore the previous release: %w", err)
		}
		state.Pending = ""
		if err := writeJSON(u.deployPath(), state, 0600); err != nil {
			return state, err
		}
	}
	return state, nil
}

func (u updater) lockedDeployment() (deployment, *os.File, error) {
	operation, err := lock(filepath.Join(u.state, "operation.lock"))
	if err != nil {
		return deployment{}, nil, err
	}
	state, err := u.loadDeployment()
	if err == nil {
		state, err = u.recover(state)
	}
	if err != nil {
		operation.Close()
		return deployment{}, nil, err
	}
	return state, operation, nil
}

func (u updater) start() error {
	state, operation, err := u.lockedDeployment()
	if err != nil {
		return err
	}
	defer operation.Close()
	rootVersion, err := readVersion(u.root)
	if err != nil {
		return err
	}
	if state.Active != "" && !newer(state.Active, rootVersion) {
		state.Active = ""
		if err := writeJSON(u.deployPath(), state, 0600); err != nil {
			return err
		}
	}
	if err := u.startVersion(state.Active, true); err != nil {
		return err
	}
	executable, err := os.Executable()
	if err != nil {
		return err
	}
	if state.Active != "" {
		name := fmt.Sprintf("kindle-updater-%s-%s", runtime.GOOS, runtime.GOARCH)
		if runtime.GOOS == "windows" {
			name += ".exe"
		}
		candidate := filepath.Join(u.releaseDir(state.Active), "scripts", "updater", "bin", name)
		if info, err := os.Stat(candidate); err == nil && info.Mode().IsRegular() {
			executable = candidate
		}
	}
	log, err := os.OpenFile(filepath.Join(u.state, "runner.log"), os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0600)
	if err != nil {
		return err
	}
	defer log.Close()
	command := exec.Command(executable, "watch")
	command.Dir = u.root
	command.Stdout, command.Stderr = log, log
	detach(command)
	if err := command.Start(); err != nil {
		return err
	}
	return command.Process.Release()
}

func (u updater) current() (string, error) {
	state, err := u.loadDeployment()
	if err != nil {
		return "", err
	}
	return readVersion(u.releaseDir(state.Active))
}

func (u updater) prepare(version string, asset releaseAsset) error {
	releases := filepath.Join(u.state, "releases")
	if err := os.MkdirAll(releases, 0700); err != nil {
		return err
	}
	stage, err := os.MkdirTemp(releases, ".stage-*")
	if err != nil {
		return err
	}
	defer os.RemoveAll(stage)
	archive := filepath.Join(stage, archiveName)
	if err := u.api.download(asset, archive); err != nil {
		return err
	}
	bundle := filepath.Join(stage, "source")
	if err := unpack(archive, bundle, version); err != nil {
		return err
	}
	target := u.releaseDir(version)
	if err := os.RemoveAll(target); err != nil {
		return err
	}
	if err := os.Rename(bundle, target); err != nil {
		return err
	}
	return u.compose(version, 25*time.Minute, "build", "app")
}

func (u updater) install(requested string) (string, error) {
	state, operation, err := u.lockedDeployment()
	if err != nil {
		return "", err
	}
	defer operation.Close()
	current, err := readVersion(u.releaseDir(state.Active))
	if err != nil {
		return "", err
	}
	version, asset, err := u.api.latest(current)
	if err != nil {
		return current, err
	}
	if version == "" || version != requested {
		return current, errors.New("the available release has changed")
	}
	if err := u.prepare(version, asset); err != nil {
		return current, err
	}
	state.Pending = version
	if err := writeJSON(u.deployPath(), state, 0600); err != nil {
		return current, err
	}
	upErr := u.startVersion(version, false)
	if upErr == nil {
		upErr = writeJSON(u.deployPath(), deployment{Active: version}, 0600)
	}
	if upErr != nil {
		_, rollbackErr := u.recover(state)
		if rollbackErr != nil {
			return current, fmt.Errorf("rollback pending: %w", errors.Join(upErr, rollbackErr))
		}
		return current, upErr
	}
	return version, nil
}

func (u updater) nextRequest() (request, bool) {
	var job request
	filename := filepath.Join(u.root, "data", requestFile)
	info, err := os.Lstat(filename)
	if err != nil || !info.Mode().IsRegular() || info.Size() > 1024 {
		return job, false
	}
	file, err := os.Open(filename)
	if err != nil {
		return job, false
	}
	decodeErr := json.NewDecoder(io.LimitReader(file, 1024)).Decode(&job)
	file.Close()
	return job, decodeErr == nil &&
		(job.Action == "check" || (job.Action == "install" && versionPattern.MatchString(job.Version))) &&
		job.Issued >= time.Now().Add(-10*time.Minute).Unix() && job.Issued <= time.Now().Add(time.Minute).Unix()
}

func (u updater) publish(value status) error {
	value.Heartbeat = time.Now().Unix()
	return writeJSON(filepath.Join(u.state, "status", "status.json"), value, 0644)
}

func (u updater) process(job request, current string) status {
	if installed, err := u.current(); err == nil {
		current = installed
	}
	if job.Action == "install" {
		version, err := u.install(job.Version)
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			return status{Current: current, Latest: job.Version, Phase: "install_failed"}
		}
		return status{Current: version, Phase: "installed"}
	}
	version, _, err := u.api.latest(current)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return status{Current: current, Phase: "check_failed"}
	}
	return status{Current: current, Latest: version, Phase: "ready"}
}

func (u updater) watch() error {
	if u.customCompose() {
		return errors.New("custom COMPOSE_FILE requires a manual update")
	}
	watcher, err := lock(filepath.Join(u.state, "watch.lock"))
	if err != nil {
		return nil
	}
	defer watcher.Close()
	// Let the launcher release its operation lock before recovery.
	time.Sleep(time.Second)
	_, operation, err := u.lockedDeployment()
	if err != nil {
		return err
	}
	operation.Close()
	current, err := u.current()
	if err != nil {
		return err
	}
	value := status{Current: current, Phase: "idle"}
	done := make(chan status, 1)
	ticker := time.NewTicker(2 * time.Second)
	defer ticker.Stop()
	nextCheck := time.Now()
	var nextHeartbeat time.Time
	busy := false
	for {
		if !busy {
			job, explicit := u.nextRequest()
			if explicit || time.Now().After(nextCheck) {
				if !explicit {
					job = request{Action: "check"}
				}
				value.Phase = "checking"
				if job.Action == "install" {
					value.Phase = "installing"
				}
				if err := u.publish(value); err != nil {
					return err
				}
				nextHeartbeat = time.Now().Add(10 * time.Second)
				if explicit {
					if err := os.Remove(filepath.Join(u.root, "data", requestFile)); err != nil {
						return err
					}
				}
				busy = true
				go func(job request, current string) { done <- u.process(job, current) }(job, value.Current)
			}
		}
		if time.Now().After(nextHeartbeat) {
			if err := u.publish(value); err != nil {
				return err
			}
			nextHeartbeat = time.Now().Add(10 * time.Second)
		}
		select {
		case <-ticker.C:
		case value = <-done:
			busy = false
			nextCheck = time.Now().Add(6 * time.Hour)
			nextHeartbeat = time.Time{}
		}
	}
}

func run() error {
	if len(os.Args) != 2 {
		return errors.New("usage: kindle-updater start|watch")
	}
	root, err := os.Getwd()
	if err != nil {
		return err
	}
	root, err = filepath.EvalSymlinks(root)
	if err != nil {
		return err
	}
	u := updater{root: root, state: filepath.Join(root, stateDirectory), api: githubClient()}
	u.runDocker = func(ctx context.Context, env []string, arguments ...string) error {
		command := exec.CommandContext(ctx, "docker", arguments...)
		command.Dir, command.Env = root, env
		command.Stdout, command.Stderr = os.Stdout, os.Stderr
		return command.Run()
	}
	if err := os.MkdirAll(filepath.Join(u.state, "status"), 0755); err != nil {
		return err
	}
	switch os.Args[1] {
	case "start":
		return u.start()
	case "watch":
		return u.watch()
	default:
		return errors.New("unknown updater command")
	}
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, "Kindle Sync updater:", err)
		os.Exit(1)
	}
}
