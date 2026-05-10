package com.example.order;

public class OrderService {
    public String format(OrderDTO order) {
        return order.getOrderCode();
    }
}
