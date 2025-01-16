import logging
import json
from pathlib import Path
import re
import asyncio
from datetime import datetime
from error_handler import with_retry, DataProcessingError, log_error, RetryConfig

logger = logging.getLogger(__name__)

class DataProcessor:
    def __init__(self):
        self.data_dir = Path('data')
        self.raw_dir = self.data_dir / 'raw'
        self.processed_dir = self.data_dir / 'processed'
        self.retry_config = RetryConfig(max_retries=3, base_delay=1.0, max_delay=15.0)
        
        # Get today's date for file naming
        self.today = datetime.now().strftime('%Y%m%d')
        self.today_dir = self.raw_dir / self.today
        
        # Ensure directories exist
        self.processed_dir.mkdir(parents=True, exist_ok=True)
        self.today_dir.mkdir(parents=True, exist_ok=True)
        
        # Setup logging when running independently
        if __name__ == '__main__':
            logging.basicConfig(
                level=logging.INFO,
                format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
        
    def load_column_tweets(self, column_file):
        """Load tweets from a column file"""
        try:
            with open(column_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading tweets from {column_file}: {str(e)}")
            return []
            
    def normalize_text(self, text):
        """Normalize special characters and symbols from text"""
        if not text:
            return text
            
        # Replace multiple spaces with single space
        text = re.sub(r'\s+', ' ', text)
        
        # Remove non-printable characters
        text = ''.join(char for char in text if char.isprintable())
        
        # Normalize unicode characters
        text = text.replace('"', '"').replace('"', '"')  # Smart quotes
        text = text.replace(''', "'").replace(''', "'")  # Smart apostrophes
        text = text.replace('…', '...')  # Ellipsis
        text = text.replace('–', '-')    # En dash
        text = text.replace('—', '-')    # Em dash
        
        # Remove URLs (optional, but they often contain special chars)
        text = re.sub(r'http[s]?://\S+', '', text)
        
        # Remove leading/trailing whitespace
        text = text.strip()
        
        return text
        
    def is_valid_tweet(self, tweet):
        """Check if a tweet is valid according to our criteria"""
        # Must have text
        if not tweet.get('text'):
            return False
            
        # Text must be at least 2 words (after normalization)
        normalized_text = self.normalize_text(tweet['text'])
        words = [w for w in normalized_text.split() if w.strip()]  # Remove empty strings
        if len(words) < 2:
            return False
            
        return True
        
    @with_retry(RetryConfig(max_retries=3, base_delay=1.0))
    async def process_tweets(self, date_str=None):
        """Process tweets with retry logic"""
        try:
            if not date_str:
                date_str = datetime.now().strftime('%Y%m%d')
                
            logger.info(f"Processing tweets for date: {date_str}")
            
            # Load and combine raw tweets
            raw_columns = await self._load_raw_tweets()
            if not raw_columns:
                logger.warning("No raw tweets found to process")
                return 0
                
            # Process tweets by column
            processed_data = self._process_raw_tweets(raw_columns)
            
            # Save processed tweets
            await self._save_processed_tweets(processed_data, date_str)
            
            return processed_data['total_tweets']
            
        except Exception as e:
            log_error(logger, e, f"Failed to process tweets for date {date_str}")
            raise DataProcessingError(f"Tweet processing failed: {str(e)}")
            
    async def _load_raw_tweets(self):
        """Load raw tweets with error handling"""
        try:
            columns = {}
            # Look in today's directory instead of raw_dir directly
            raw_dir = self.raw_dir / self.today
            if not raw_dir.exists():
                logger.error(f"Raw directory not found for date {self.today}")
                return {}
                
            total_tweets = 0
            for file in raw_dir.glob('column_*.json'):
                try:
                    column_id = file.stem.split('_')[1]  # Get column number from filename
                    with open(file, 'r') as f:
                        tweets = json.load(f)
                        columns[column_id] = tweets
                        total_tweets += len(tweets)
                except Exception as e:
                    log_error(logger, e, f"Failed to load tweets from {file}")
                    continue
                    
            logger.info(f"Loaded {total_tweets} raw tweets from {len(columns)} columns in {raw_dir}")
            return columns
            
        except Exception as e:
            log_error(logger, e, "Failed to load raw tweets")
            raise DataProcessingError(f"Raw tweet loading failed: {str(e)}")
            
    def _remove_duplicates(self, tweets):
        """Remove duplicate tweets based on tweet ID"""
        try:
            seen_ids = set()
            unique_tweets = []
            
            for tweet in tweets:
                tweet_id = tweet.get('id')
                if not tweet_id or tweet_id in seen_ids:
                    continue
                    
                seen_ids.add(tweet_id)
                unique_tweets.append(tweet)
                
            logger.info(f"Removed {len(tweets) - len(unique_tweets)} duplicate tweets")
            return unique_tweets
            
        except Exception as e:
            log_error(logger, e, "Failed to remove duplicates")
            raise DataProcessingError(f"Duplicate removal failed: {str(e)}")
            
    def _normalize_tweet(self, tweet):
        """Normalize tweet text and metadata"""
        try:
            normalized = tweet.copy()
            
            # Normalize text
            if 'text' in normalized:
                normalized['text'] = self.normalize_text(normalized['text'])
                
            # Add processing metadata
            normalized['processed_at'] = datetime.now().isoformat()
            
            return normalized
            
        except Exception as e:
            log_error(logger, e, f"Failed to normalize tweet: {tweet.get('id', 'unknown')}")
            raise DataProcessingError(f"Tweet normalization failed: {str(e)}")
            
    def _process_raw_tweets(self, columns):
        """Process raw tweets with error handling"""
        try:
            processed_data = {
                'total_tweets': 0,
                'columns': {}
            }
            
            for column_id, tweets in columns.items():
                logger.info(f"Processing column {column_id} with {len(tweets)} tweets")
                
                # Remove duplicates within this column
                unique_tweets = self._remove_duplicates(tweets)
                
                # Filter invalid tweets
                valid_tweets = [
                    tweet for tweet in unique_tweets
                    if self._is_valid_tweet(tweet)
                ]
                
                # Normalize text
                normalized_tweets = [
                    self._normalize_tweet(tweet)
                    for tweet in valid_tweets
                ]
                
                if normalized_tweets:
                    processed_data['columns'][column_id] = normalized_tweets
                    processed_data['total_tweets'] += len(normalized_tweets)
                    logger.info(f"Column {column_id}: {len(tweets)} → {len(normalized_tweets)} tweets after processing")
                
            return processed_data
            
        except Exception as e:
            log_error(logger, e, "Failed to process raw tweets")
            raise DataProcessingError(f"Tweet processing failed: {str(e)}")
            
    def _is_valid_tweet(self, tweet):
        """Validate tweet with error handling"""
        try:
            if not tweet.get('text'):
                return False
                
            text = tweet['text'].strip()
            words = text.split()
            
            return len(words) >= 2
            
        except Exception as e:
            log_error(logger, e, f"Failed to validate tweet: {tweet.get('id', 'unknown')}")
            return False
            
    async def _save_processed_tweets(self, processed_data, date_str):
        """Save processed tweets with error handling"""
        try:
            self.processed_dir.mkdir(parents=True, exist_ok=True)
            output_file = self.processed_dir / f'processed_tweets_{date_str}.json'
            
            with open(output_file, 'w') as f:
                json.dump(processed_data, f, indent=2)
                
            logger.info(f"Saved {processed_data['total_tweets']} processed tweets across {len(processed_data['columns'])} columns")
                
        except Exception as e:
            log_error(logger, e, f"Failed to save processed tweets for date {date_str}")
            raise DataProcessingError(f"Failed to save processed tweets: {str(e)}")

if __name__ == "__main__":
    # Setup and run processor
    processor = DataProcessor()
    
    # Allow processing specific date from command line
    import sys
    date_to_process = sys.argv[1] if len(sys.argv) > 1 else None
    
    # Run the async process_tweets function
    asyncio.run(processor.process_tweets(date_to_process)) 