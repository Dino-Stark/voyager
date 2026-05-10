package com.shop;

public class UserService {
    public void register(UserDTO user) {
        String name = user.getUserName();
        user.setEmail("test@example.com");
        user.setAge(25);
    }

    public String formatDisplayName(UserDTO user) {
        return user.getUserName() + " <" + user.getEmail() + ">";
    }

    public void printInfo(UserDTO user) {
        System.out.println(user.getUserName());
        System.out.println(user.getEmail());
    }
}
