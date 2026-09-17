// Package main serves the inventory API.
//
// A non-primary language on purpose: at baseline this repository must produce
// nothing, and later packages are measured by how far off zero they move it.
package main

import (
	"encoding/json"
	"net/http"
)

// MaxReservation is the largest quantity a single reservation may hold.
const MaxReservation = 500

func listItems(w http.ResponseWriter, r *http.Request) {
	json.NewEncoder(w).Encode(inventory)
}

func getItem(w http.ResponseWriter, r *http.Request) {
	json.NewEncoder(w).Encode(inventory[r.URL.Query().Get("sku")])
}

// reserveItem holds stock against an order. A reservation above MaxReservation
// is refused so that one order cannot empty the warehouse.
func reserveItem(w http.ResponseWriter, r *http.Request) {
	var body struct {
		SKU      string `json:"sku"`
		Quantity int    `json:"quantity"`
	}
	json.NewDecoder(r.Body).Decode(&body)
	if body.Quantity > MaxReservation {
		http.Error(w, "reservation exceeds the per-order limit", http.StatusBadRequest)
		return
	}
	inventory[body.SKU] -= body.Quantity
}

func releaseItem(w http.ResponseWriter, r *http.Request) {
	var body struct {
		SKU      string `json:"sku"`
		Quantity int    `json:"quantity"`
	}
	json.NewDecoder(r.Body).Decode(&body)
	inventory[body.SKU] += body.Quantity
}

var inventory = map[string]int{}

func main() {
	http.HandleFunc("/items", listItems)
	http.HandleFunc("/item", getItem)
	http.HandleFunc("/reserve", reserveItem)
	http.HandleFunc("/release", releaseItem)
	http.ListenAndServe(":8080", nil)
}
