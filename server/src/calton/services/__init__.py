"""Resource service layers.

A service owns one resource's business rules and takes the request's ``Session`` as its
first argument. It never opens a session of its own: the router hands the same one to the
policy and the service so a check made after a write sees that write (design §2.3).
"""
