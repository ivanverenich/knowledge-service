"""PostgreSQL persistence for the stored domain records.

Domain values stay free of SQLAlchemy. `schema` owns the stored table
shapes; each other module translates one kind of domain value to and from
rows and holds the statements that read and write it.
"""
