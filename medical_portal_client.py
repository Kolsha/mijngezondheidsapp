"""
Medical Portal Client for Huisartsenpraktijk Oostenburg

A Python class to interact with the medical portal at:
https://huisartsenpraktijk-oostenburg.app.tetra.nl/en/

Features:
- Authentication with email and password
- Session persistence using cookies
- List messages from medical file
- Ask questions to the doctor
- Automatic session management
"""

import requests
import json
import os
import logging
from typing import Optional, Dict, List, Any, Union
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
import time
from datetime import datetime, timedelta
import urllib3
from dataclasses import dataclass

# Suppress SSL warnings for testing
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


@dataclass
class TwoFactorAuthData:
    """Data class to hold 2FA authentication state."""
    form_data: Dict[str, str]
    sms_url: str
    timestamp: datetime


class MedicalPortalClient:
    """
    Client for interacting with the Huisartsenpraktijk Oostenburg medical portal.
    
    This class provides methods to authenticate, manage sessions, list messages,
    and ask questions through the medical portal.
    """
    
    def __init__(self, base_url: str = "https://huisartsenpraktijk-oostenburg.app.tetra.nl", 
                 session_file: str = "medical_portal_session.json"):
        """
        Initialize the medical portal client.
        
        Args:
            base_url: Base URL of the medical portal
            session_file: Path to file for storing session data
        """
        self.base_url = base_url.rstrip('/')
        self.session_file = session_file
        self.session = requests.Session()
        self.is_authenticated = False
        
        # Set up logging
        logging.basicConfig(level=logging.INFO)
        self.logger = logging.getLogger(__name__)
        
        # Set default headers to mimic a real browser
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        })
        
        # Disable SSL verification for testing (should be enabled in production)
        self.session.verify = False
        
        # Load existing session if available
        self._load_session()
    
    def _load_session(self) -> None:
        """Load session data from file if it exists."""
        if os.path.exists(self.session_file):
            self.logger.info(f"Loading session from {self.session_file}")
            try:
                with open(self.session_file, 'r') as f:
                    session_data = json.load(f)
                
                # Restore cookies
                for cookie in session_data.get('cookies', []):
                    self.session.cookies.set(**cookie)
                
                # Check if session is still valid
                if self._is_session_valid():
                    self.is_authenticated = True
                    self.logger.info("Session loaded successfully")
                else:
                    self.logger.info("Session expired, will need to re-authenticate")
                    
            except (json.JSONDecodeError, KeyError) as e:
                # clear session
                self.session.cookies.clear()
                self.logger.warning(f"Failed to load session: {e}")
    
    def _save_session(self) -> None:
        """Save current session data to file."""
        try:
            session_data = {
                'cookies': [
                    {
                        'name': cookie.name,
                        'value': cookie.value,
                        'domain': cookie.domain,
                        'path': cookie.path,
                        'secure': cookie.secure,
                        'expires': cookie.expires,
                    }
                    for cookie in self.session.cookies
                ],
                'timestamp': datetime.now().isoformat()
            }
            
            with open(self.session_file, 'w') as f:
                json.dump(session_data, f, indent=2)
                
            self.logger.info("Session saved successfully")
            
        except Exception as e:
            self.logger.error(f"Failed to save session: {e}")
    
    def _is_session_valid(self) -> bool:
        """Check if the current session is still valid."""
        try:
            # Try to access a protected page to check if we're still authenticated
            response = self.session.get(f"{self.base_url}/en/safe", allow_redirects=False)
            return response.status_code != 303  # 303 means redirect to login
        except Exception:
            return False
    
    def _get_form_data(self, html_content: str) -> Dict[str, str]:
        """Extract form data including CSRF tokens from HTML."""
        soup = BeautifulSoup(html_content, 'html.parser')
        form_data = {}
        
        # Find hidden inputs
        hidden_inputs = soup.find_all('input', type='hidden')
        for input_field in hidden_inputs:
            name = input_field.get('name')
            value = input_field.get('value', '')
            if name:
                form_data[name] = value
        
        return form_data
    
    def begin_authentication(self, email: str, password: str) -> Union[bool, TwoFactorAuthData]:
        """
        Begin authentication with the medical portal.
        
        Args:
            email: User's email address
            password: User's password
            
        Returns:
            True if authentication successful (no 2FA required)
            False if authentication failed (invalid credentials)
            TwoFactorAuthData if 2FA is required
        """
        try:
            # First, get the login page to extract form data
            login_url = f"{self.base_url}/en/login/account"
            response = self.session.get(login_url)
            response.raise_for_status()
            
            # Extract form data including CSRF tokens
            form_data = self._get_form_data(response.text)

            # print(form_data)
            # Add login credentials
            form_data.update({
                'name': email,
                'password': password
            })
            
            # Submit login form
            response = self.session.post(login_url, data=form_data, allow_redirects=True)
            response.raise_for_status()
            
            # Check if we're redirected to SMS verification page
            if '/login/sms' in response.url:
                self.logger.info("2FA required - redirected to SMS verification page")
                
                # Extract form data from SMS page
                sms_form_data = self._get_form_data(response.text)
                # print(sms_form_data)
                
                # Create 2FA data object
                twofa_data = TwoFactorAuthData(
                    form_data=sms_form_data,
                    sms_url=f"{self.base_url}/en/login/sms",
                    timestamp=datetime.now()
                )
                
                return twofa_data
            
            # Check if login was successful by looking for redirect to login page
            elif '/login' in response.url:
                self.logger.error("Authentication failed - redirected to login page")
                return False
            
            # Check for success indicators in the response
            if 'Sign in' in response.text and 'Enter your email address' in response.text:
                self.logger.error("Authentication failed - still on login page")
                return False
            
            self.is_authenticated = True
            self._save_session()
            self.logger.info("Authentication successful")
            return True
            
        except requests.RequestException as e:
            self.logger.error(f"Authentication failed: {e}")
            return False
        except Exception as e:
            self.logger.error(f"Unexpected error during authentication: {e}")
            return False
    
    def complete_two_factor_auth(self, twofa_data: TwoFactorAuthData, sms_code: str) -> bool:
        """
        Complete two-factor authentication with SMS code.
        
        Args:
            twofa_data: TwoFactorAuthData object from begin_authentication()
            sms_code: SMS verification code
            
        Returns:
            True if 2FA successful, False otherwise
        """
        try:
            # Add SMS code to form data
            form_data = twofa_data.form_data.copy()
            form_data.update({
                'sms': sms_code
            })
            
            # Submit SMS verification
            response = self.session.post(twofa_data.sms_url, data=form_data, allow_redirects=True)
            response.raise_for_status()
            
            # Check if SMS verification was successful
            if '/login' in response.url and '/sms' not in response.url:
                self.logger.error("SMS verification failed - redirected back to login")
                return False
            
            # Check for success indicators
            if 'Sign in' in response.text and 'Enter your email address' in response.text:
                self.logger.error("SMS verification failed - still on login page")
                return False
            
            self.is_authenticated = True
            self._save_session()
            self.logger.info("2FA authentication successful")
            return True
            
        except requests.RequestException as e:
            self.logger.error(f"2FA authentication failed: {e}")
            return False
        except Exception as e:
            self.logger.error(f"Unexpected error during 2FA authentication: {e}")
            return False
    
    def list_messages(self, folder: str = 'inbox') -> List[Dict[str, Any]]:
        """
        List messages from the correspondence tab.
        
        Args:
            folder: 'inbox' or 'archive' to specify which folder to retrieve messages from
        
        Returns:
            List of message dictionaries with details
        """
        if not self.is_authenticated:
            self.logger.error("Not authenticated. Please call authenticate() first.")
            return []
        
        try:
            # Access the correspondence page
            correspondence_url = f"{self.base_url}/en/correspondence?tab=correspondence"
            response = self.session.get(correspondence_url)
            response.raise_for_status()
            
            # Parse the HTML to extract messages
            soup = BeautifulSoup(response.text, 'html.parser')
            messages = []
            
            # Find the specific folder (inbox or archive)
            folder_div = soup.find('div', id=folder)
            if not folder_div:
                self.logger.warning(f"Folder '{folder}' not found")
                return []
            
            # Look for the button-list div within the folder
            button_list = folder_div.find('div', class_='button-list')
            if not button_list:
                self.logger.warning(f"Button list not found in folder '{folder}'")
                return []
            
            # Look for message links in the button-list
            message_links = button_list.find_all('a', href=True)
            
            for link in message_links:
                # Extract message details from the link
                message = {
                    'type': 'E-consult',
                    'date': None,
                    'time': None,
                    'url': None,
                    'id': None,
                    'subject': None,
                    'answered': None
                }
                
                # Extract URL and ID from href
                href = link.get('href', '')
                if '/safe/consult?' in href:
                    message['url'] = urljoin(self.base_url, href)
                    # Extract ID from URL parameters
                    if 'id=' in href:
                        message['id'] = href.split('id=')[1].split('&')[0]
                    # Extract date from URL parameters
                    if 'date=' in href:
                        message['date'] = href.split('date=')[1].split('&')[0]
                
                # Extract subject/title
                strong_elem = link.find('strong')
                if strong_elem:
                    message['subject'] = strong_elem.get_text(strip=True)
                
                # Extract answered status from data-reaction attribute
                data_reaction = link.get('data-reaction')
                if data_reaction is not None:
                    message['answered'] = data_reaction.lower() == 'true'
                
                # Extract timestamp
                span_elem = link.find('span')
                if span_elem:
                    timestamp_text = span_elem.get_text(strip=True)
                    # Parse date and time from timestamp
                    if timestamp_text:
                        parts = timestamp_text.split()
                        if len(parts) >= 2:
                            if parts[0].lower() == 'today':
                                # Handle "Today" case
                                message['date'] = 'Today'
                                if len(parts) >= 2:
                                    message['time'] = parts[1]
                            else:
                                # Handle regular date format like "3 October 2025 15:56"
                                if len(parts) >= 4:
                                    # Format: "3 October 2025 15:56"
                                    message['date'] = parts[0] + ' ' + parts[1] + ' ' + parts[2]  # "3 October 2025"
                                    message['time'] = parts[3]  # "15:56"
                                elif len(parts) >= 3:
                                    # Format: "3 October 15:56" (no year)
                                    message['date'] = parts[0] + ' ' + parts[1]  # "3 October"
                                    message['time'] = parts[2]  # "15:56"
                
                messages.append(message)
            
            self.logger.info(f"Retrieved {len(messages)} messages from {folder}")
            return messages
            
        except requests.RequestException as e:
            self.logger.error(f"Failed to retrieve messages: {e}")
            return []
        except Exception as e:
            self.logger.error(f"Unexpected error while retrieving messages: {e}")
            return []
    
    def get_message_details(self, message_url: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed content of a specific message by accessing its URL.
        
        Args:
            message_url: Full URL to the message (e.g., from list_messages)
            
        Returns:
            Dictionary with detailed message content or None if failed
        """
        if not self.is_authenticated:
            self.logger.error("Not authenticated. Please call authenticate() first.")
            return None
        
        try:
            response = self.session.get(message_url)
            response.raise_for_status()
            
            # Parse the HTML to extract message content
            soup = BeautifulSoup(response.text, 'html.parser')
            
            message_details = {
                'url': message_url,
                'content': '',
                'question': '',
                'answer': '',
                'date': None,
                'time': None,
                'sender': None,
                'subject': None,
                'attachments': []
            }
            
            # Extract date from the small-spacer-bottom paragraph
            date_elem = soup.find('p', class_='small-spacer-bottom')
            if date_elem:
                date_text = date_elem.get_text(strip=True)
                if date_text.startswith('Date:'):
                    message_details['date'] = date_text.replace('Date:', '').strip()
            
            # Extract main content from the question and answer sections
            question_elem = soup.find('div', {'data-speech': 'question'})
            answer_elem = soup.find('div', {'data-speech': 'answer'})
            
            # Extract question content
            if question_elem:
                question_content = question_elem.find('div', class_='content')
                if question_content:
                    message_details['question'] = question_content.get_text(strip=True)
            
            # Extract answer content
            if answer_elem:
                answer_content = answer_elem.find('div')
                if answer_content:
                    message_details['answer'] = answer_content.get_text(strip=True)
            
            # Combine question and answer for backward compatibility
            content_parts = []
            if message_details['question']:
                content_parts.append(f"Question: {message_details['question']}")
            if message_details['answer']:
                content_parts.append(f"Answer: {message_details['answer']}")
            message_details['content'] = '\n\n'.join(content_parts)
            
            # Extract sender from answer section (health care provider)
            if answer_elem:
                sender_h2 = answer_elem.find('h2')
                if sender_h2:
                    message_details['sender'] = sender_h2.get_text(strip=True)
            
            # Extract subject from main h1
            subject_elem = soup.find('h1', class_='no-spacer-bottom')
            if subject_elem:
                message_details['subject'] = subject_elem.get_text(strip=True)
            
            # Look for attachments in the content
            attachment_links = soup.find_all('a', href=lambda x: x and any(ext in x.lower() for ext in ['.pdf', '.doc', '.docx', '.jpg', '.png']))
            for link in attachment_links:
                attachment = {
                    'name': link.get_text(strip=True),
                    'url': urljoin(self.base_url, link.get('href', ''))
                }
                message_details['attachments'].append(attachment)
            
            self.logger.info(f"Retrieved message details from {message_url}")
            return message_details
            
        except requests.RequestException as e:
            self.logger.error(f"Failed to retrieve message details: {e}")
            return None
        except Exception as e:
            self.logger.error(f"Unexpected error while retrieving message details: {e}")
            return None
    
    def get_all_messages(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        Get messages from both inbox and archive folders.
        
        Returns:
            Dictionary with 'inbox' and 'archive' keys containing lists of messages
        """
        if not self.is_authenticated:
            self.logger.error("Not authenticated. Please call authenticate() first.")
            return {'inbox': [], 'archive': []}
        
        try:
            # Get inbox messages
            inbox_messages = self.list_messages('inbox')
            
            # Get archive messages
            archive_messages = self.list_messages('archive')
            
            return {
                'inbox': inbox_messages,
                'archive': archive_messages
            }
            
        except Exception as e:
            self.logger.error(f"Unexpected error while retrieving all messages: {e}")
            return {'inbox': [], 'archive': []}
    
    def ask_question(self, question: str, draft: bool = False, attachment_path: str = None) -> bool:
        """
        Ask a question to the doctor.
        
        Args:
            question: The question message (max 600 characters)
            draft: Whether to save as draft (default: False)
            attachment_path: Optional path to file to attach
            
        Returns:
            True if question submitted successfully, False otherwise
        """
        if not self.is_authenticated:
            self.logger.error("Not authenticated. Please call authenticate() first.")
            return False
        
        try:
            # First, get the consult page to extract form data
            consult_url = f"{self.base_url}/en/consult"
            response = self.session.get(consult_url)
            response.raise_for_status()
            
            # Extract form data including CSRF tokens
            form_data = self._get_form_data(response.text)
            
            # Prepare form data
            form_data.update({
                'question': question,
                'draft': 'on' if draft else ''
            })
            
            # Prepare files for upload if attachment is provided
            files = None
            if attachment_path and os.path.exists(attachment_path):
                files = {
                    'attachment': open(attachment_path, 'rb')
                }
            
            # Submit the question
            response = self.session.post(consult_url, data=form_data, files=files, allow_redirects=True)
            response.raise_for_status()
            
            # Close file if it was opened
            if files:
                files['attachment'].close()
            
            # Check if submission was successful
            if 'question' in response.text.lower() and ('submitted' in response.text.lower() or 'sent' in response.text.lower()):
                self.logger.info("Question submitted successfully")
                return True
            elif 'error' in response.text.lower():
                self.logger.error("Question submission failed - error in response")
                return False
            else:
                # If we can't determine success/failure, assume success if no error
                self.logger.info("Question submitted (status unclear)")
                return True
            
        except requests.RequestException as e:
            self.logger.error(f"Failed to submit question: {e}")
            return False
        except Exception as e:
            self.logger.error(f"Unexpected error while submitting question: {e}")
            return False
    
    def logout(self) -> None:
        """Logout and clear session data."""
        try:
            # Try to logout if possible
            logout_url = f"{self.base_url}/en/logout"
            self.session.get(logout_url)
        except Exception:
            pass  # Ignore logout errors
        
        # Clear session data
        self.session.cookies.clear()
        self.is_authenticated = False
        
        # Remove session file
        if os.path.exists(self.session_file):
            os.remove(self.session_file)
        
        self.logger.info("Logged out successfully")
    
    def get_patient_info(self) -> Optional[Dict[str, Any]]:
        """
        Get patient information from the portal.
        
        Returns:
            Dictionary with patient information or None if not available
        """
        if not self.is_authenticated:
            self.logger.error("Not authenticated. Please call authenticate() first.")
            return None
        
        try:
            # Try to access patient settings or profile page
            settings_url = f"{self.base_url}/en/my-settings"
            response = self.session.get(settings_url)
            response.raise_for_status()
            # print(response.text)
            
            soup = BeautifulSoup(response.text, 'html.parser')
            patient_info = {}
            
            # Extract patient information from the page
            # This will need to be adjusted based on actual HTML structure
            name_elem = soup.find(['h1', 'h2'], string=lambda text: text and 'welcome' in text.lower())
            if name_elem:
                patient_info['name'] = name_elem.get_text(strip=True)
            
            # Look for other patient details
            info_containers = soup.find_all(['div', 'span'], class_=lambda x: x and 'patient' in x.lower())
            for container in info_containers:
                text = container.get_text(strip=True)
                if text and len(text) > 2:
                    patient_info['details'] = patient_info.get('details', [])
                    patient_info['details'].append(text)
            
            return patient_info if patient_info else None
            
        except requests.RequestException as e:
            self.logger.error(f"Failed to retrieve patient info: {e}")
            return None
        except Exception as e:
            self.logger.error(f"Unexpected error while retrieving patient info: {e}")
            return None
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - save session on exit."""
        if self.is_authenticated:
            self._save_session()
    
    def __del__(self):
        """Destructor - save session on cleanup."""
        try:
            if hasattr(self, 'is_authenticated') and self.is_authenticated:
                self._save_session()
        except Exception:
            pass  # Ignore errors during cleanup
