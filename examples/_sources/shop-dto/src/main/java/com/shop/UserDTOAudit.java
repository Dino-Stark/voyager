package com.shop;

public class UserDTOAudit {
    private UserDTO user;

    public UserDTOAudit(UserDTO user) {
        this.user = user;
    }

    public UserDTO getUser() {
        return user;
    }
}
