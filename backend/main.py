import asyncio
import uvicorn
from typing import List, Dict, Any
import multiprocessing
import structlog
import signal
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

# Add backend to Python path
backend_dir = Path(__file__).parent
sys.path.append(str(backend_dir))

from shared.db.database import DatabaseConnection
from shared.logger import setup_logger

logger = setup_logger("virtual_butler")

class ServiceManager:
    """Manages multiple FastAPI services"""
    
    def __init__(self):
        self.services = [
            {
                "name": "chatbot",
                "module": "chatbot.main:app",
                "host": "0.0.0.0",
                "port": 8001
            },
            {
                "name": "work_orders",
                "module": "work_orders.main:app",
                "host": "0.0.0.0",
                "port": 8002
            },
            {
                "name": "notifications",
                "module": "notifications.main:app",
                "host": "0.0.0.0",
                "port": 8003
            }
        ]
        self.processes: List[multiprocessing.Process] = []
        self.executor = ThreadPoolExecutor(max_workers=len(self.services))
        self.shutdown_event = asyncio.Event()

    def run_service(self, service: Dict[str, Any]) -> None:
        """Run a single service using uvicorn"""
        try:
            logger.info(f"Starting {service['name']} service on port {service['port']}")
            uvicorn.run(
                service["module"],
                host=service["host"],
                port=service["port"],
                reload=True,
                reload_dirs=["backend"],
                log_level="info"
            )
        except Exception as e:
            logger.error(f"Error in {service['name']} service: {str(e)}")

    async def start_all(self) -> None:
        """Start all services"""
        try:
            # Verify database connection first
            await DatabaseConnection.connect()
            logger.info("Database connection verified")

            # Start each service in a separate process
            for service in self.services:
                process = multiprocessing.Process(
                    target=self.run_service,
                    args=(service,)
                )
                process.start()
                self.processes.append(process)
                logger.info(f"{service['name']} service process started")

            # Wait for shutdown signal
            await self.shutdown_event.wait()

        except Exception as e:
            logger.error(f"Error starting services: {str(e)}")
            await self.shutdown()
        finally:
            await DatabaseConnection.close()

    async def shutdown(self) -> None:
        """Shutdown all services gracefully"""
        logger.info("Shutting down services...")
        
        for process in self.processes:
            if process.is_alive():
                process.terminate()
                process.join()
                logger.info(f"Process {process.pid} terminated")

        self.executor.shutdown(wait=True)
        self.shutdown_event.set()
        logger.info("All services shut down successfully")

def signal_handler(signum, frame):
    """Handle shutdown signals"""
    logger.info(f"Received signal {signum}")
    asyncio.get_event_loop().run_until_complete(manager.shutdown())
    sys.exit(0)

if __name__ == "__main__":
    # Configure signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Create and start service manager
    manager = ServiceManager()
    
    try:
        logger.info("Starting Virtual Butler Backend Services")
        asyncio.run(manager.start_all())
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
        asyncio.run(manager.shutdown())
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        asyncio.run(manager.shutdown())
        sys.exit(1)