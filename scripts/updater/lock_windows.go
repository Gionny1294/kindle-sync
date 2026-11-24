package main

import (
	"os"
	"os/exec"
	"syscall"
)

func lock(path string) (*os.File, error) {
	name, err := syscall.UTF16PtrFromString(path)
	if err != nil {
		return nil, err
	}
	handle, err := syscall.CreateFile(name, syscall.GENERIC_READ|syscall.GENERIC_WRITE, 0, nil, syscall.OPEN_ALWAYS, syscall.FILE_ATTRIBUTE_NORMAL, 0)
	if err != nil {
		return nil, err
	}
	return os.NewFile(uintptr(handle), path), nil
}

func detach(command *exec.Cmd) {
	const detachedProcess = 0x00000008
	command.SysProcAttr = &syscall.SysProcAttr{CreationFlags: detachedProcess, HideWindow: true}
}
