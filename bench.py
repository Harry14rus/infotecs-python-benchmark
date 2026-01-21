import argparse
import asyncio
import re
import sys
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple
import aiohttp
import aiofiles

@dataclass
class RequestResult:
    host: str
    success: bool
    status_code: Optional[int]
    duration: float
    error: Optional[str] = None

@dataclass
class HostStats:
    host: str
    success_count: int = 0
    failed_count: int = 0
    error_count: int = 0
    times: List[float] = None

    def __post_init__(self):
        if self.times is None:
            self.times = []

    def add_result(self, result: RequestResult):
        if result.error:
            self.error_count += 1
        elif result.status_code and 400 <= result.status_code < 600:
            self.failed_count += 1
        else:
            self.success_count += 1
        
        if result.duration > 0:
            self.times.append(result.duration)

    @property
    def min_time(self) -> float:
        return min(self.times) if self.times else 0

    @property
    def max_time(self) -> float:
        return max(self.times) if self.times else 0

    @property
    def avg_time(self) -> float:
        return sum(self.times) / len(self.times) if self.times else 0

class ServerBenchmark:
    def __init__(self):
        self.stats = {}
        self.url_pattern = re.compile(
            r'^https?://(?:www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b(?:[-a-zA-Z0-9()@:%_\+.~#?&//=]*)$'
        )

    def validate_url(self, url: str) -> bool:
        """Проверка формата URL"""
        return bool(self.url_pattern.match(url))

    def parse_args(self):
        parser = argparse.ArgumentParser(
            description='Тестирование доступности серверов по HTTP'
        )
        
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument('-H', '--hosts', 
                          help='Список хостов через запятую (без пробелов)')
        group.add_argument('-F', '--file', 
                          help='Путь к файлу со списком адресов')
        
        parser.add_argument('-C', '--count', type=int, default=1,
                          help='Количество запросов на каждый хост (по умолчанию 1)')
        parser.add_argument('-O', '--output',
                          help='Путь к файлу для сохранения результатов')
        
        args = parser.parse_args()
        
        # Валидация count
        if args.count <= 0:
            print("Ошибка: параметр count должен быть положительным числом")
            sys.exit(1)
        
        return args

    async def fetch_url(self, session: aiohttp.ClientSession, 
                       url: str, timeout: int = 10) -> RequestResult:
        """Выполнение одного HTTP-запроса"""
        start_time = time.time()
        try:
            async with session.get(url, timeout=timeout) as response:
                duration = time.time() - start_time
                success = 200 <= response.status < 400
                return RequestResult(
                    host=url,
                    success=success,
                    status_code=response.status,
                    duration=duration
                )
        except aiohttp.ClientError as e:
            duration = time.time() - start_time
            return RequestResult(
                host=url,
                success=False,
                status_code=None,
                duration=duration,
                error=str(e)
            )
        except asyncio.TimeoutError:
            duration = time.time() - start_time
            return RequestResult(
                host=url,
                success=False,
                status_code=None,
                duration=duration,
                error="Timeout"
            )
        except Exception as e:
            duration = time.time() - start_time
            return RequestResult(
                host=url,
                success=False,
                status_code=None,
                duration=duration,
                error=f"Unexpected error: {str(e)}"
            )

    async def test_host(self, host: str, count: int):
        """Тестирование одного хоста"""
        if not self.validate_url(host):
            print(f"Предупреждение: пропускаем некорректный URL - {host}")
            return

        if host not in self.stats:
            self.stats[host] = HostStats(host=host)

        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            tasks = [self.fetch_url(session, host) for _ in range(count)]
            results = await asyncio.gather(*tasks)
            
            for result in results:
                self.stats[host].add_result(result)

    async def run_tests(self, hosts: List[str], count: int):
        """Запуск всех тестов"""
        tasks = [self.test_host(host, count) for host in hosts]
        await asyncio.gather(*tasks)

    def print_stats(self, output_file: Optional[str] = None):
        """Вывод статистики"""
        output_lines = []
        
        for host, stat in self.stats.items():
            output_lines.append(f"\n{'='*60}")
            output_lines.append(f"Host: {host}")
            output_lines.append(f"{'-'*60}")
            output_lines.append(f"Success:        {stat.success_count}")
            output_lines.append(f"Failed (4xx/5xx): {stat.failed_count}")
            output_lines.append(f"Errors:         {stat.error_count}")
            output_lines.append(f"Min time:       {stat.min_time:.3f} сек")
            output_lines.append(f"Max time:       {stat.max_time:.3f} сек")
            output_lines.append(f"Avg time:       {stat.avg_time:.3f} сек")
            output_lines.append(f"{'='*60}")
        
        output_text = "\n".join(output_lines)
        
        if output_file:
            try:
                with open(output_file, 'w', encoding='utf-8') as f:
                    f.write(output_text)
                print(f"Результаты сохранены в файл: {output_file}")
            except Exception as e:
                print(f"Ошибка при записи в файл: {e}")
                print(output_text)
        else:
            print(output_text)

    async def read_hosts_from_file(self, filepath: str) -> List[str]:
        """Чтение хостов из файла"""
        try:
            async with aiofiles.open(filepath, mode='r', encoding='utf-8') as f:
                content = await f.read()
            
            hosts = [
                line.strip() 
                for line in content.split('\n') 
                if line.strip() and not line.startswith('#')
            ]
            return hosts
        except Exception as e:
            print(f"Ошибка при чтении файла {filepath}: {e}")
            sys.exit(1)

    async def main(self):
        """Основная функция"""
        args = self.parse_args()
        
        # Получение списка хостов
        if args.file:
            hosts = await self.read_hosts_from_file(args.file)
        else:
            hosts = [h.strip() for h in args.hosts.split(',') if h.strip()]
        
        # Проверка хостов
        if not hosts:
            print("Ошибка: не указаны хосты для тестирования")
            sys.exit(1)
        
        valid_hosts = []
        for host in hosts:
            if self.validate_url(host):
                valid_hosts.append(host)
            else:
                print(f"Предупреждение: пропускаем некорректный URL - {host}")
        
        if not valid_hosts:
            print("Ошибка: нет валидных хостов для тестирования")
            sys.exit(1)
        
        print(f"Тестирование {len(valid_hosts)} хостов...")
        print(f"Количество запросов на каждый хост: {args.count}")
        print("Это может занять некоторое время...\n")
        
        # Запуск тестов
        await self.run_tests(valid_hosts, args.count)
        
        # Вывод результатов
        self.print_stats(args.output)

def main():
    """Точка входа"""
    benchmark = ServerBenchmark()
    asyncio.run(benchmark.main())

if __name__ == "__main__":
    main()