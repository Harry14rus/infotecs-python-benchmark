import argparse
import asyncio
import aiohttp
import aiofiles
import time
import sys
from dataclasses import dataclass
from typing import List, Optional
import re

@dataclass
class RequestResult:
    url: str
    success: bool
    status: Optional[int]
    duration: float
    error: Optional[str] = None

class AsyncHttpBenchmark:
    def __init__(self):
        self.semaphore = asyncio.Semaphore(10)  # Ограничение одновременных запросов
        self.url_pattern = re.compile(r'^https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+')
    
    async def make_request(self, session: aiohttp.ClientSession, url: str) -> RequestResult:
        """Асинхронный HTTP запрос с ограничением через семафор"""
        async with self.semaphore:  # Ограничиваем количество одновременных запросов
            start_time = time.time()
            try:
                async with session.get(url, timeout=10) as response:
                    duration = time.time() - start_time
                    return RequestResult(
                        url=url,
                        success=200 <= response.status < 400,
                        status=response.status,
                        duration=duration
                    )
            except asyncio.TimeoutError:
                return RequestResult(url=url, success=False, status=None, 
                                   duration=time.time()-start_time, error="Timeout")
            except Exception as e:
                return RequestResult(url=url, success=False, status=None,
                                   duration=time.time()-start_time, error=str(e))
    
    async def benchmark_host(self, session: aiohttp.ClientSession, 
                           url: str, count: int) -> List[RequestResult]:
        """Запускает несколько асинхронных запросов к одному хосту"""
        tasks = [self.make_request(session, url) for _ in range(count)]
        return await asyncio.gather(*tasks)
    
    async def run_benchmark(self, urls: List[str], count: int, output_file: Optional[str] = None):
        """Основная асинхронная функция тестирования"""
        print(f"🚀 Запуск асинхронного тестирования {len(urls)} хостов...")
        print(f"📊 По {count} запросов на каждый хост")
        print("⏳ Выполнение запросов параллельно...\n")
        
        start_total = time.time()
        
        async with aiohttp.ClientSession() as session:
            # Создаем задачи для всех хостов
            all_tasks = []
            for url in urls:
                all_tasks.append(self.benchmark_host(session, url, count))
            
            # Запускаем все задачи параллельно
            all_results = await asyncio.gather(*all_tasks)
        
        total_time = time.time() - start_total
        
        # Обработка и вывод результатов
        self.print_results(urls, all_results, total_time, output_file)
    
    def print_results(self, urls: List[str], all_results: List[List[RequestResult]], 
                     total_time: float, output_file: Optional[str]):
        """Вывод результатов с подсчетом статистики"""
        
        output_lines = []
        output_lines.append("=" * 70)
        output_lines.append("РЕЗУЛЬТАТЫ АСИНХРОННОГО ТЕСТИРОВАНИЯ СЕРВЕРОВ")
        output_lines.append("=" * 70)
        output_lines.append(f"Общее время выполнения: {total_time:.2f} сек")
        output_lines.append(f"Количество хостов: {len(urls)}")
        output_lines.append("=" * 70 + "\n")
        
        for url, results in zip(urls, all_results):
            success = [r for r in results if r.success]
            failed = [r for r in results if r.status and 400 <= r.status < 600]
            errors = [r for r in results if r.error]
            times = [r.duration for r in results if r.duration > 0]
            
            output_lines.append(f"📡 Хост: {url}")
            output_lines.append("-" * 50)
            output_lines.append(f"✅ Успешных:       {len(success)}")
            output_lines.append(f"⚠️  С ошибкой (4xx/5xx): {len(failed)}")
            output_lines.append(f"❌ Ошибок соединения: {len(errors)}")
            
            if times:
                output_lines.append(f"⏱️  Время ответа:")
                output_lines.append(f"   Минимальное:  {min(times):.3f} сек")
                output_lines.append(f"   Максимальное: {max(times):.3f} сек")
                output_lines.append(f"   Среднее:      {sum(times)/len(times):.3f} сек")
            
            if errors:
                output_lines.append(f"   Ошибки: {errors[0].error[:50]}...")
            
            output_lines.append("=" * 70 + "\n")
        
        output_text = "\n".join(output_lines)
        
        if output_file:
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(output_text)
            print(f"📄 Результаты сохранены в файл: {output_file}")
        else:
            print(output_text)
    
    async def read_urls_from_file(self, filename: str) -> List[str]:
        """Асинхронное чтение URL из файла"""
        async with aiofiles.open(filename, 'r', encoding='utf-8') as f:
            content = await f.read()
        return [line.strip() for line in content.split('\n') 
                if line.strip() and not line.startswith('#')]

async def main():
    parser = argparse.ArgumentParser(description='Асинхронный бенчмарк HTTP серверов')
    parser.add_argument('-H', '--hosts', help='Хосты через запятую (без пробелов)')
    parser.add_argument('-F', '--file', help='Файл со списком URL (по одному на строку)')
    parser.add_argument('-C', '--count', type=int, default=1, 
                       help='Количество запросов на хост (по умолчанию: 1)')
    parser.add_argument('-O', '--output', help='Файл для сохранения результатов')
    
    args = parser.parse_args()
    
    # Проверка аргументов
    if not args.hosts and not args.file:
        print("❌ Ошибка: укажите хосты через -H или файл через -F")
        sys.exit(1)
    
    if args.hosts and args.file:
        print("❌ Ошибка: укажите только один из параметров -H или -F")
        sys.exit(1)
    
    benchmark = AsyncHttpBenchmark()
    
    # Получение списка URL
    if args.file:
        urls = await benchmark.read_urls_from_file(args.file)
    else:
        urls = [url.strip() for url in args.hosts.split(',')]
    
    # Валидация URL
    valid_urls = []
    for url in urls:
        if benchmark.url_pattern.match(url):
            valid_urls.append(url)
        else:
            print(f"⚠️  Пропущен некорректный URL: {url}")
    
    if not valid_urls:
        print("❌ Ошибка: нет валидных URL для тестирования")
        sys.exit(1)
    
    # Запуск асинхронного тестирования
    await benchmark.run_benchmark(valid_urls, args.count, args.output)

if __name__ == "__main__":
    # Запуск асинхронной программы
    asyncio.run(main())