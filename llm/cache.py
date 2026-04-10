"""
Intelligent caching system for SDX Agent
"""

import json
import hashlib
from pathlib import Path
from typing import Optional, Any
from datetime import datetime, timedelta


class CacheManager:
    """Manages caching of AI responses"""
    
    def __init__(self, cache_dir: str = ".cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)
        self.ttl = timedelta(hours=24)
    
    def _hash_query(self, query: str) -> str:
        """Create hash of query for cache key"""
        return hashlib.md5(query.encode()).hexdigest()
    
    def get(self, query: str) -> Optional[str]:
        """Get cached response if exists and not expired"""
        cache_file = self.cache_dir / f"{self._hash_query(query)}.json"
        
        if not cache_file.exists():
            return None
        
        try:
            with open(cache_file, 'r') as f:
                data = json.load(f)
            
            # Check if cache is expired
            created_at = datetime.fromisoformat(data['created_at'])
            if datetime.now() - created_at > self.ttl:
                cache_file.unlink()
                return None
            
            return data['response']
        except Exception:
            return None
    
    def set(self, query: str, response: str):
        """Cache a response"""
        cache_file = self.cache_dir / f"{self._hash_query(query)}.json"
        
        data = {
            'query': query,
            'response': response,
            'created_at': datetime.now().isoformat()
        }
        
        try:
            with open(cache_file, 'w') as f:
                json.dump(data, f)
        except Exception:
            pass  # Silently fail cache writes
    
    def clear(self):
        """Clear all cache"""
        for f in self.cache_dir.glob("*.json"):
            f.unlink()
