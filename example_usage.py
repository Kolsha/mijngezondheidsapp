#!/usr/bin/env python3
"""
Example usage of the MedicalPortalClient class.

This script demonstrates how to use the MedicalPortalClient to:
1. Authenticate with the medical portal
2. List messages from the medical file
3. Ask questions to the doctor
4. Manage session persistence

Usage:
    python example_usage.py
"""

import os
import getpass
from medical_portal_client import MedicalPortalClient
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


def main():
    """Main example function."""
    print("Medical Portal Client Example")
    print("=" * 40)
    
    # Initialize the client
    email = os.getenv('LOGIN')
    password = os.getenv('PASSWORD')
    
    client = MedicalPortalClient()
    
    # Check if we're already authenticated
    if client.is_authenticated:
        print("✓ Already authenticated (session loaded from file)")
    else:
        print("Authentication required")
        
        # Get credentials from user
        email = email or input("Enter your email: ").strip()
        if not email:
            print("Email is required")
            return
        
        password = password or getpass.getpass("Enter your password: ")
        if not password:
            print("Password is required")
            return
        
        # Authenticate with 2FA support
        print("Authenticating...")
        
        # Begin authentication
        auth_result = client.begin_authentication(email, password)
        
        if auth_result is True:
            print("✓ Authentication successful")
        elif auth_result is False:
            print("✗ Authentication failed")
            return
        else:
            # 2FA required
            print("📱 2FA required - SMS verification needed")
            sms_code = input("Enter SMS verification code: ").strip()
            
            if client.complete_two_factor_auth(auth_result, sms_code):
                print("✓ 2FA successful - Authentication completed")
            else:
                print("✗ 2FA failed")
                return
    
    print("\n" + "=" * 40)
    print("Available actions:")
    print("1. List messages from medical file")
    print("2. View detailed message content")
    print("3. List archived messages")
    print("4. Ask a question to the doctor")
    print("5. Get patient information")
    print("6. Logout")
    print("7. Exit")
    
    while True:
        try:
            choice = input("\nEnter your choice (1-7): ").strip()
            
            if choice == '1':
                print("\nRetrieving inbox messages...")
                messages = client.list_messages('inbox')
                
                if messages:
                    print(f"\nFound {len(messages)} messages in inbox:")
                    print("-" * 50)
                    for i, msg in enumerate(messages, 1):
                        print(f"\nMessage {i}:")
                        print(f"Type: {msg.get('type', 'Unknown')}")
                        if msg.get('subject'):
                            print(f"Subject: {msg['subject']}")
                        if msg.get('date'):
                            print(f"Date: {msg['date']}")
                        if msg.get('time'):
                            print(f"Time: {msg['time']}")
                        if msg.get('id'):
                            print(f"ID: {msg['id']}")
                        if msg.get('url'):
                            print(f"URL: {msg['url']}")
                else:
                    print("No messages found in inbox")
            
            elif choice == '2':
                print("\nView detailed message content")
                print("First, let's list your messages to select one:")
                
                messages = client.list_messages('inbox')
                if not messages:
                    print("No messages available")
                    continue
                
                print(f"\nAvailable messages:")
                for i, msg in enumerate(messages, 1):
                    print(f"{i}. {msg.get('subject', 'E-consult')} - {msg.get('date', 'Unknown date')}")
                
                try:
                    msg_choice = int(input("\nEnter message number to view details: ")) - 1
                    if 0 <= msg_choice < len(messages):
                        selected_msg = messages[msg_choice]
                        if selected_msg.get('url'):
                            print(f"\nRetrieving details for message {msg_choice + 1}...")
                            details = client.get_message_details(selected_msg['url'])
                            
                            if details:
                                print("\n" + "=" * 50)
                                print("MESSAGE DETAILS")
                                print("=" * 50)
                                if details.get('subject'):
                                    print(f"Subject: {details['subject']}")
                                if details.get('date'):
                                    print(f"Date: {details['date']}")
                                if details.get('sender'):
                                    print(f"From: {details['sender']}")
                                print(f"\nContent:")
                                print("-" * 30)
                                print(details.get('content', 'No content available'))
                                
                                if details.get('attachments'):
                                    print(f"\nAttachments ({len(details['attachments'])}):")
                                    for att in details['attachments']:
                                        print(f"  - {att['name']}: {att['url']}")
                            else:
                                print("Failed to retrieve message details")
                        else:
                            print("No URL available for this message")
                    else:
                        print("Invalid message number")
                except ValueError:
                    print("Please enter a valid number")
                except Exception as e:
                    print(f"Error: {e}")
            
            elif choice == '3':
                print("\nRetrieving archived messages...")
                messages = client.list_messages('archive')
                
                if messages:
                    print(f"\nFound {len(messages)} archived messages:")
                    print("-" * 50)
                    for i, msg in enumerate(messages, 1):
                        print(f"\nMessage {i}:")
                        print(f"Type: {msg.get('type', 'Unknown')}")
                        if msg.get('subject'):
                            print(f"Subject: {msg['subject']}")
                        if msg.get('date'):
                            print(f"Date: {msg['date']}")
                        if msg.get('time'):
                            print(f"Time: {msg['time']}")
                        if msg.get('id'):
                            print(f"ID: {msg['id']}")
                else:
                    print("No archived messages found")
            
            elif choice == '4':
                print("\nAsk a question to the doctor")
                question = input("Enter your question (max 600 characters): ").strip()
                if not question:
                    print("Question is required")
                    continue
                
                if len(question) > 600:
                    print("Question is too long (max 600 characters)")
                    continue
                
                draft = input("Save as draft? (y/N): ").strip().lower() == 'y'
                
                attachment = input("Attachment file path (optional): ").strip()
                if attachment and not os.path.exists(attachment):
                    print("Attachment file not found")
                    continue
                
                print("Submitting question...")
                if client.ask_question(question, draft, attachment if attachment else None):
                    print("✓ Question submitted successfully")
                else:
                    print("✗ Failed to submit question")
            
            elif choice == '5':
                print("\nRetrieving patient information...")
                patient_info = client.get_patient_info()
                
                if patient_info:
                    print("\nPatient Information:")
                    print("-" * 20)
                    for key, value in patient_info.items():
                        if isinstance(value, list):
                            print(f"{key}:")
                            for item in value:
                                print(f"  - {item}")
                        else:
                            print(f"{key}: {value}")
                else:
                    print("No patient information available or unable to retrieve")
            
            elif choice == '6':
                print("\nLogging out...")
                client.logout()
                print("✓ Logged out successfully")
                break
            
            elif choice == '7':
                print("\nExiting...")
                break
            
            else:
                print("Invalid choice. Please enter 1-7.")
        
        except KeyboardInterrupt:
            print("\n\nExiting...")
            break
        except Exception as e:
            print(f"\nAn error occurred: {e}")
            print("Please try again.")


def demo_with_context_manager():
    """Demonstrate using the client as a context manager."""
    print("\n" + "=" * 40)
    print("Context Manager Example")
    print("=" * 40)
    
    # Using the client as a context manager ensures session is saved
    with MedicalPortalClient() as client:
        if not client.is_authenticated:
            print("Please authenticate first using the main menu")
            return
        
        print("✓ Using context manager - session will be saved automatically")
        
        # List messages
        messages = client.list_messages()
        print(f"Retrieved {len(messages)} messages")
        
        # Ask a question
        success = client.ask_question(
            "This is a test question from the Python client"
        )
        print(f"Question submission: {'Success' if success else 'Failed'}")
    
    print("✓ Context manager exited - session saved")


if __name__ == "__main__":
    try:
        main()
        
        # Uncomment the line below to also run the context manager demo
        # demo_with_context_manager()
        
    except Exception as e:
        print(f"Fatal error: {e}")
        print("Please check your internet connection and try again.")
