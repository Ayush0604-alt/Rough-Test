package com.example.orders;

import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/orders")
public class OrderController {

    private final OrderService service;

    public OrderController(OrderService service) {
        this.service = service;
    }

    @GetMapping("/{id}")
    public Order find(@PathVariable String id) {
        return service.lookup(id);
    }

    @PostMapping
    public Order create(@RequestBody OrderRequest request) {
        if (request.getQuantity() > 100) {
            throw new IllegalArgumentException("quantity exceeds limit");
        }
        return service.place(request);
    }
}
