from selenium.webdriver.chrome.options import Options
from selenium import webdriver
from typing import List, Tuple
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from markdownify import markdownify as md
import requests
import time
import random
import os
import psycopg2
import tabulate
import csv

load_dotenv(dotenv_path=r".")


def create_headless_browser():
    options = Options()
    options.add_argument('--headless')
    assert options.headless  # assert Operating in headless mode
    return webdriver.Chrome(options=options)


class DB:
    def __init__(self):
        self.__POSTGRES_DB = os.getenv("POSTGRES_DB")
        self.__POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD")
        self.__POSTGRES_USER = os.getenv("POSTGRES_USER")
        self.__connect()
        self.__drop_tables(['proxy', 'drug'])
        self.__create_tables()

    def __connect(self):
        try:
            self.connection = psycopg2.connect(user=self.__POSTGRES_USER,
                                               password=self.__POSTGRES_PASSWORD,
                                               host="127.0.0.1",
                                               port="5432",
                                               database=self.__POSTGRES_DB)
            self.cursor = self.connection.cursor()
            print("connected to db successfully")
        except (Exception, psycopg2.Error) as error:
            print("failed to connect to db", error)

    def __create_tables(self):
        if not self.connection:
            return
        queries = []
        # proxies(proxy)
        proxy_table_query = 'CREATE TABLE proxy(proxy TEXT PRIMARY KEY)'
        queries.append((proxy_table_query, "proxy"))

        # drug(drug_title, drug_url, drug_article, drug_picture)
        drug_table_query = '''
                CREATE TABLE drug(
                    drug_title TEXT PRIMARY KEY,
                    drug_url TEXT NOT NULL,
                    drug_article TEXT NOT NULL,
                    drug_picture TEXT NOT NULL)
        '''
        queries.append((drug_table_query, "drug"))
        
        for query in queries:
            query_text, table_name = query
            self.cursor.execute(query_text)
            self.connection.commit()
            print(f"Table {table_name} created successfully in PostgreSQL db")

    def __close_connection(self):
        if not self.connection:
            return

        self.cursor.close()
        self.connection.close()
        print("PostgreSQL connection is closed")

    def __check_existence(self, column_name, column_value, table_name):
        check_query = f"SELECT {column_name} FROM {table_name} WHERE {column_name} == {column_value}"
        self.cursor.execute(check_query)
        return len(self.cursor.fetchall()) != 0

    def seed_drug_table(self, drug_title: str, drug: Tuple):
        if self.__check_existence("drug_title", drug_title, "drug"):
            return

        drug_url, drug_article, drug_picture = drug
        seeding_genre_query = f""" 
                INSERT INTO artist (drug_title, drug_url, drug_article, drug_picture)  
                VALUES ({drug_title}, {drug_url}, {drug_article}, {drug_picture})
        """

        self.cursor.execute(seeding_genre_query)
        self.connection.commit()
        print(f"seeding drug table with {drug_title}")

    def seed_proxy_table(self, proxy: str):
        if self.__check_existence("proxy", proxy, "proxy"):
            return

        seeding_genre_query = f"INSERT INTO proxy (proxy) VALUES ({proxy})"
        self.cursor.execute(seeding_genre_query)
        self.connection.commit()
        print(f"seeding proxy table with {proxy}")

    def __drop_tables(self, tables_names):
        for table_name in tables_names:
            drop_table_query = f"DROP TABLE IF EXISTS {table_name} CASCADE"
            self.cursor.execute(drop_table_query)
            self.connection.commit()
            print(f"table {table_name} dropped")

    def get_data(self, table_name):
        if not self.connection:
            return

        row_select_query = f"SELECT * FROM {table_name}"
        self.cursor.execute(row_select_query)
        rows = [row[0] for row in self.cursor.fetchall()]
        columns_select_query = f"SELECT column_name FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = '{table_name}'"
        self.cursor.execute(columns_select_query)
        columns = [col[0] for col in self.cursor.fetchall()]
        print("="*28, f"@ rows in {table_name} table @", "="*28)
        print(tabulate.tabulate(rows, headers=columns, tablefmt="psql"))
        print("\n")


class ScrapeProxies:
    def __init__(self):
        self.db: DB = DB()
        self.page_url = 'https://free-proxy-list.net/'
        self.browser = self.browser = create_headless_browser()

    def __get_table_proxies(self, source_code: str) -> List:
        soup = BeautifulSoup(source_code, 'html.parser')
        table_rows = soup.select('#proxylisttable tbody tr')
        proxies = []
        for row in table_rows:
            columns = row.findAll("td")
            if columns[-2].getText() == 'yes' and columns[-4].getText() == 'elite proxy':
                proxy = f'https://{columns[0].getText()}:{columns[1].getText()}'
                proxies.append({
                    'requests_count': 0,
                    'start_time': None,
                    'proxy': proxy
                })
                self.db.seed_proxy_table(proxy)
        print(f'@@@ proxy {proxy} scraped and saved successfully @@@')
        return proxies

    def __get_proxies(self):
        next_button = self.browser.find_element(value='proxylisttable_next')
        proxies = []
        while 'disabled' not in next_button.get_attribute('class'):
            source_code = self.browser.page_source
            tables_proxies = self.__get_table_proxies(source_code)
            proxies.extend(tables_proxies)
            next_button.find_element_by_tag_name("a").click()
            next_button = self.browser.find_element(
                value='proxylisttable_next')
        return proxies

    def get_data(self):
        data = self.db.get_data("proxy")
        if data != []:
            return data
        self.browser.get(self.page_url)
        time.sleep(1)
        return self.__get_proxies()


class Scraper:
    def __init__(self, take: int) -> None:
        # self.db: DB = DB()
        self.take: int = take
        self.base_url_format: str = f'https://www.drugs.com'
        self.columns: List[str] = ['title', 'url', 'article', 'picture']
        # self.proxies = ScrapeProxies().get_data()
        self.proxies = []

    # TODO: debug request proxy method
    def __request_proxy(self, page_url: str) -> str:
        current_proxy, request = self.proxies[-1], None
        try:
            print(f'@@@ trying {current_proxy["proxy"]} @@@')
            # * If current max request number is reached use another proxy:
            if current_proxy['requests_count'] > 450 or \
                (current_proxy['start_time'] and
                    time.time() - current_proxy['start_time'] > 3600):
                print(f'@@@ proxy {current_proxy["proxy"]} failed @@@')
                self.proxies.pop()
                self.__set_proxy(page_url)
            proxies = {
                'http': current_proxy['proxy'],
                'https': current_proxy['proxy'],
            }
            random_delay = random.randint(0, 21)
            time.sleep(random_delay)
            request = requests.get(page_url, timeout=5, proxies=proxies)
            if self.proxies[-1]['requests_count'] == 0:
                self.proxies[-1]['start_time'] = time.time()
            self.proxies[-1]['requests_count'] += 1
        except:
            print(f'@@@ proxy {current_proxy["proxy"]} failed @@@')
            self.proxies.pop()
            self.__request_data(page_url)
        return request.content or ''

    def __request_data(self, page_url: str) -> str:
        # # TODO: handle redirect(server and client side) on requesting a web page
        # # handling redirect: page 221: https://bit.ly/33K2OcG
        print(f'@@@ fetching data from {page_url} @@@')
        if self.proxies == []:
            random_delay = random.randint(0, 21)
            time.sleep(random_delay)
            return requests.get(page_url, timeout=5).content or ''
        return self.__request_proxy(page_url)

    def __get_drugs_urls(self, page_url: str) -> set[str]:
        content = self.__request_data(page_url)
        soup = BeautifulSoup(content, 'html.parser')
        return {f'{self.base_url_format}{url["href"]}' for url in soup.select(".ddc-list-column-2 li a")}

    def __get_cats_urls(self, page_url: str) -> List[str]:
        page_content: str = self.__request_data(page_url)
        soup = BeautifulSoup(page_content, 'html.parser')
        return [f'{self.base_url_format}{suffix["href"]}' for suffix in soup.select(".ddc-paging li a")]

    def __get_urls(self) -> List[str]:
        page_urls: List[str] = [
            f'{self.base_url_format}/alpha/{chr(index + 97)}.html' for index in range(26)]
        page_urls.append(f'{self.base_url_format}/alpha/0-9.html')
        for page in page_urls:
            page_urls.extend(self.__get_cats_urls(page))

        drugs_urls: set[str] = set()
        for page in page_urls:
            drugs_urls += self.__get_drugs_urls(page)
        return list(drugs_urls)

    def __scrape_drug_data(self, drug_url: str) -> List[str]:
        page_content = self.__request_data(drug_url)
        soup = BeautifulSoup(page_content, 'html.parser')
        drug_title = soup.select_one('.contentBox h1').getText()
        # TODO: debug get article section
        #! ------------------------------
        content = soup.select_one('.contentBox').findChildren()
        while content != [] and not str(content[0]).startswith("<h2"):
            content = content[1:]
        sections = []
        for section in content:
            if 'class' in section and ' '.join(section['class']).find('display-ad') > -1:
                continue
            sections.append(str(section))
        markdown_article = md(''.join(sections), heading_style="ATX")
        #! ------------------------------
        drug_pict = soup.select_one('.drugImageHolder img')
        if drug_pict:
            drug_pict = drug_pict['data-src']
        return [drug_title, drug_url, markdown_article, drug_pict]

    def __save_to_csv(self, drug_data):
        file_name: str = f'data/articles/{drug_data[0]}.md'
        with open(file_name, mode="w+") as file:
            try:
                file.write(drug_data[-2])
            except: pass
        
        db_file_name: str = r'data/db.csv'
        if not os.path.exists(db_file_name):
            os.system(f'echo {",".join(self.columns)} > {db_file_name}')

        with open(db_file_name, mode="a+") as csv_file:
            drug_data[-2] = os.path.join(os.getcwd(), file_name)
            writer = csv.writer(csv_file)
            writer.writerow(drug_data)

    def __scrape_save_drugs_data(self, drugs_urls: List[str]) -> None:
        for drug_url in drugs_urls:
            drug_data = self.__scrape_drug_data(drug_url)
            self.__save_to_csv(drug_data)
            #* save to postgresql db -- docker is required
            #! ------------------------------
            #! self.db.seed_drug_table(drug_data[0], tuple(drug_data[1:]))
            #! ------------------------------

    def run(self) -> None:
        urls: List[str] = self.__get_urls()
        self.__scrape_save_drugs_data(urls[:self.take])

if __name__ == '__main__':
    scraper: Scraper = Scraper(take=10)
    scraper.run()