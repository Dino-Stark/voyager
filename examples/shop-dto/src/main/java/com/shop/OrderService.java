package com.shop;

public class OrderService {
    private UserDTO buyer;
    private UserService userService = new UserService();

    public void createOrder(OrderDTO order, UserDTO user) {
        order.setOrderId("ORD-001");
        order.setTotalPrice(99.9);
        this.buyer = user;
        String buyerName = buyer.getUserName();
    }

    public String getBuyerName() {
        return buyer.getUserName();
    }

    public String getBuyerLabel(UserDTO user) {
        return userService.formatDisplayName(user);
    }
}
