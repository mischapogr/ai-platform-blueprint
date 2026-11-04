// Inspect public Go artifact metadata and linked Tempo functions without executing it.
package main

import (
	"crypto/sha256"
	"debug/buildinfo"
	"debug/elf"
	"debug/gosym"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"sort"
	"strings"
)

func main() {
	if len(os.Args) != 2 {
		panic("one artifact path required")
	}
	path := os.Args[1]
	raw, err := os.ReadFile(path)
	if err != nil {
		panic(err)
	}
	sum := sha256.Sum256(raw)
	info, err := buildinfo.ReadFile(path)
	if err != nil {
		panic(err)
	}
	file, err := elf.Open(path)
	if err != nil {
		panic(err)
	}
	defer file.Close()
	text := file.Section(".text")
	if text == nil {
		panic("ELF text missing")
	}
	pcln := file.Section(".gopclntab")
	if pcln == nil {
		pcln = file.Section(".data.rel.ro.gopclntab")
	}
	if pcln == nil {
		panic("Go function table missing")
	}
	data, err := pcln.Data()
	if err != nil {
		panic(err)
	}
	table, err := gosym.NewTable(nil, gosym.NewLineTable(data, text.Addr))
	if err != nil {
		panic(err)
	}
	functions := []string{}
	for _, function := range table.Funcs {
		if strings.Contains(function.Name, "github.com/grafana/tempo/") {
			functions = append(functions, function.Name)
		}
	}
	sort.Strings(functions)
	modules := []any{}
	for _, module := range info.Deps {
		if module.Path == "github.com/grafana/tempo" {
			modules = append(modules, module)
		}
	}
	output := map[string]any{"binary_sha256": hex.EncodeToString(sum[:]), "go_version": info.GoVersion,
		"main": info.Main, "settings": info.Settings, "tempo_modules": modules,
		"tempo_functions": functions, "function_table_count": len(table.Funcs)}
	encoded, err := json.MarshalIndent(output, "", "  ")
	if err != nil {
		panic(err)
	}
	fmt.Println(string(encoded))
}
