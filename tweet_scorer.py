import logging
import json
from pathlib import Path
from datetime import datetime
import asyncio
import aiohttp

logger = logging.getLogger(__name__)

class TweetScorer:
    def __init__(self, config):
        self.config = config
        self.data_dir = Path('data')
        self.processed_dir = self.data_dir / 'processed'
        self.api_key = config['deepseek_api_key']
        self.api_url = "https://api.deepseek.com/v1/chat/completions"  # Update with actual endpoint
        
        # Category mappings
        self.categories = {
            '0': 'NEAR Ecosystem',
            '1': 'Polkadot Ecosystem',
            '2': 'Arbitrum Ecosystem',
            '3': 'IOTA Ecosystem',
            '4': 'AI Agents',
            '5': 'DefAI'
        }
        
    async def score_tweet(self, tweet, category):
        """Score a single tweet using Deepseek API"""
        try:
            prompt = f"""
            Please analyze this tweet's importance specifically for the {category} category and provide scores in JSON format.

            Tweet Content:
            Text: {tweet['text']}
            Author: {tweet['authorHandle']}
            {"Quoted content: " + tweet['quotedContent']['text'] if tweet.get('quotedContent') else ""}
            {"Reposted content: " + tweet['repostedContent']['text'] if tweet.get('repostedContent') else ""}

            Scoring criteria:
            1. Relevance (0-1): How directly does this tweet relate to {category}? Consider mentions of key projects, technologies, or developments specific to this category.
            2. Significance (0-1): How important is this news/update for the {category}? Consider the scale and scope of impact within this specific ecosystem.
            3. Impact (0-1): What potential effects could this have on the {category}'s development or adoption? Consider both short and long-term implications.
            4. Ecosystem relevance (0-1): How does this contribute to the overall growth and development of the {category}? Consider partnerships, integrations, or technological advancements.

            Your reasoning must explain why this tweet matters specifically for the {category}.

            EXAMPLE JSON OUTPUT:
            {{
                "relevance": 0.8,
                "significance": 0.7,
                "impact": 0.9,
                "ecosystem_relevance": 0.85,
                "average_score": 0.81,
                "reasoning": "This announcement directly impacts {category} by [specific reason]. It represents a significant development because [category-specific importance]. The potential impact on the ecosystem is substantial due to [specific implications for this category]."
            }}
            """
            
            payload = {
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "response_format": {"type": "json_object"},
                "max_tokens": 1000
            }
            
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(self.api_url, json=payload, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        # Extract the content directly from the response
                        scores = json.loads(data['choices'][0]['message']['content'])
                        # Add the tweet ID to the scores
                        scores['tweet_id'] = tweet['id']
                        return scores
                    else:
                        error_text = await response.text()
                        logger.error(f"API error: {response.status} - {error_text}")
                        return None
            
        except Exception as e:
            logger.error(f"Error scoring tweet {tweet['id']}: {str(e)}")
            return None
            
    def _is_valid_tweet(self, tweet):
        """Validate tweet structure and content"""
        try:
            # Check required fields
            required_fields = ['id', 'text', 'authorHandle', 'url']
            if not all(field in tweet for field in required_fields):
                return False
                
            # Check text content
            if not tweet['text'] or len(tweet['text'].split()) < 2:
                return False
                
            # Check for valid ID
            if not tweet['id'] or not str(tweet['id']).strip():
                return False
                
            # Check for valid author
            if not tweet['authorHandle'] or not str(tweet['authorHandle']).strip():
                return False
                
            return True
                
        except Exception as e:
            logger.error(f"Error validating tweet: {str(e)}")
            return False
            
    async def process_tweets(self, date_str):
        """Process all tweets for a given date"""
        try:
            # Load tweets from processed file
            file_path = self.processed_dir / f'processed_tweets_{date_str}.json'
            if not file_path.exists():
                logger.error(f"No processed tweets file found for date {date_str}")
                return
            
            with open(file_path, 'r') as f:
                data = json.load(f)
            
            # Process tweets in chunks of 5 per column
            chunk_size = 5
            tasks = []
            
            for column_id, tweets in data['columns'].items():
                # Skip empty columns
                if not tweets:
                    logger.info(f"Skipping empty column {column_id}")
                    continue
                    
                category = self.categories.get(column_id)
                if not category:
                    logger.warning(f"No category mapping found for column {column_id}")
                    continue
                
                # Validate tweets before scoring
                valid_tweets = [t for t in tweets if self._is_valid_tweet(t)]
                if not valid_tweets:
                    logger.warning(f"No valid tweets found in column {column_id}")
                    continue
                    
                logger.info(f"Processing {len(valid_tweets)}/{len(tweets)} valid tweets in column {column_id}")
                
                # Process tweets in chunks
                for i in range(0, len(valid_tweets), chunk_size):
                    chunk = valid_tweets[i:i + chunk_size]
                    chunk_tasks = [self.score_tweet(tweet, category) for tweet in chunk]
                    tasks.extend(chunk_tasks)
            
            # Process chunks in parallel with semaphore to limit concurrency
            semaphore = asyncio.Semaphore(10)  # Limit to 10 concurrent API calls
            async def score_with_semaphore(task):
                async with semaphore:
                    return await task
            
            logger.info(f"Processing {len(tasks)} tweets in chunks...")
            scores = await asyncio.gather(*[score_with_semaphore(task) for task in tasks])
            
            # Filter and update tweets based on scores
            filtered_data = {
                'date': date_str,
                'total_tweets': 0,
                'columns': {}
            }
            
            for column_id, tweets in data['columns'].items():
                filtered_tweets = []
                for tweet in tweets:
                    # Skip invalid tweets
                    if not self._is_valid_tweet(tweet):
                        continue
                        
                    for score in scores:
                        if score and score.get('tweet_id') == tweet['id']:
                            if score.get('relevance', 0) > 0.7:  # Only keep tweets with high relevance
                                tweet['scores'] = score
                                filtered_tweets.append(tweet)
                            break
                
                if filtered_tweets:
                    filtered_data['columns'][column_id] = filtered_tweets
                    filtered_data['total_tweets'] += len(filtered_tweets)
                    logger.info(f"Kept {len(filtered_tweets)}/{len(tweets)} tweets for column {column_id}")
            
            # Save filtered data
            with open(file_path, 'w') as f:
                json.dump(filtered_data, f, indent=2)
                
            logger.info(f"Saved {filtered_data['total_tweets']} high-scoring tweets to {file_path}")
            
        except Exception as e:
            logger.error(f"Error processing tweets: {str(e)}")
            
if __name__ == "__main__":
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Get date to process - either from args or use today's date
    import sys
    date_to_process = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime('%Y%m%d')
    
    logger.info(f"Processing tweets for date: {date_to_process}")
    
    # Load config and run scorer
    import os
    from dotenv import load_dotenv
    load_dotenv()
    
    config = {
        'deepseek_api_key': os.getenv('DEEPSEEK_API_KEY')
    }
    
    scorer = TweetScorer(config)
    asyncio.run(scorer.process_tweets(date_to_process)) 