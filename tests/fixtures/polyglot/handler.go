package main

import (
	"encoding/json"
	"net/http"
	"os"
)

const MaxPageSize = 50

type Store struct {
	dsn string
}

func NewStore() *Store {
	return &Store{dsn: os.Getenv("DB_DSN")}
}

func (s *Store) Load(id string) ([]byte, error) {
	return json.Marshal(map[string]string{"id": id})
}

func listItems(w http.ResponseWriter, r *http.Request) {
	store := NewStore()
	data, err := store.Load(r.URL.Query().Get("id"))
	if err != nil {
		http.Error(w, "failed", http.StatusInternalServerError)
		return
	}
	w.Write(data)
}

func main() {
	http.HandleFunc("/items", listItems)
	http.ListenAndServe(":8080", nil)
}
