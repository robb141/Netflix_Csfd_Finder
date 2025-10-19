import sqlite3


class Movies:
    def __init__(self):
        self.__db_name = 'netflix'
        self.__connection = sqlite3.connect('movies.db')
        self.__c = self.__connection.cursor()

    def __del__(self):
        self.__connection.close()

    def create_and_insert_table(self, movies):
        try:
            self.__c.execute(f'CREATE TABLE {self.__db_name} (movie_id INTEGER PRIMARY KEY, title TEXT, year INTEGER, category TEXT, rating REAL, audio TEXT, duration TEXT, genre TEXT, seen BOOLEAN NOT NULL default 0)')
        except sqlite3.OperationalError:
            print('Table already exists!')
        self.__c.execute(f'DELETE FROM {self.__db_name}')
        self.__c.executemany(f'INSERT INTO {self.__db_name}(title, year, category, rating, audio, duration, genre, seen) VALUES (?,?,?,?,?,?,?,?)', movies)
        self.__connection.commit()

    def get_data(self, condition):
        self.__c.execute(f'SELECT * FROM {self.__db_name} WHERE {condition}')
        selected_movies = self.__c.fetchall()
        return selected_movies


if __name__ == '__main__':
    movie = Movies()
    for m in movie.get_data("category = 'movies' AND seen = 0 ORDER BY RANDOM() LIMIT 10"):
        print(f'{m[1]} ({m[2]}) {m[3]}')
