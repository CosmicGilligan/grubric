#!/usr/bin/env python3
"""
Canvas API Test Script
Tests Canvas API connectivity and rubric retrieval
"""

import requests
import json
from canvas_rubric_api import load_canvas_credentials, CanvasRubricAPI

def test_canvas_connection():
    """Test basic Canvas API connection"""
    print("Testing Canvas API Connection...")
    print("=" * 50)
    
    try:
        # Load credentials
        canvas_url, api_token = load_canvas_credentials()
        print(f"Canvas URL: {canvas_url}")
        print(f"API Token: {'*' * (len(api_token) - 4) + api_token[-4:]}")
        
        # Test basic connection
        headers = {'Authorization': f'Bearer {api_token}'}
        response = requests.get(f"{canvas_url}/api/v1/users/self", headers=headers)
        
        if response.status_code == 200:
            user_data = response.json()
            print(f"✅ API Connection successful!")
            print(f"   User: {user_data.get('name', 'Unknown')}")
            print(f"   User ID: {user_data.get('id', 'Unknown')}")
            return True
        else:
            print(f"❌ API Connection failed: {response.status_code}")
            print(f"   Response: {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ Connection test failed: {e}")
        return False

def test_course_access(course_id):
    """Test access to a specific course"""
    print(f"\nTesting Course Access: {course_id}")
    print("=" * 50)
    
    try:
        canvas_url, api_token = load_canvas_credentials()
        headers = {'Authorization': f'Bearer {api_token}'}
        
        response = requests.get(f"{canvas_url}/api/v1/courses/{course_id}", headers=headers)
        
        if response.status_code == 200:
            course_data = response.json()
            print(f"✅ Course access successful!")
            print(f"   Course: {course_data.get('name', 'Unknown')}")
            print(f"   Course Code: {course_data.get('course_code', 'Unknown')}")
            print(f"   Enrollment Term: {course_data.get('enrollment_term_id', 'Unknown')}")
            return True
        else:
            print(f"❌ Course access failed: {response.status_code}")
            print(f"   Response: {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ Course access test failed: {e}")
        return False

def test_course_assignments(course_id):
    """List all assignments for a course"""
    print(f"\nListing Assignments for Course: {course_id}")
    print("=" * 50)
    
    try:
        canvas_url, api_token = load_canvas_credentials()
        headers = {'Authorization': f'Bearer {api_token}'}
        
        params = {
            'include[]': ['rubric'],
            'per_page': 50
        }
        
        response = requests.get(f"{canvas_url}/api/v1/courses/{course_id}/assignments", 
                              headers=headers, params=params)
        
        if response.status_code == 200:
            assignments = response.json()
            print(f"✅ Found {len(assignments)} assignments")
            
            for assignment in assignments:
                assignment_id = assignment.get('id')
                assignment_name = assignment.get('name', 'Unnamed')
                has_rubric = 'rubric' in assignment and assignment['rubric'] is not None
                rubric_count = len(assignment.get('rubric', [])) if has_rubric else 0
                
                print(f"   ID: {assignment_id} | {assignment_name} | Rubric: {'Yes' if has_rubric else 'No'} ({rubric_count} criteria)")
            
            return assignments
        else:
            print(f"❌ Failed to get assignments: {response.status_code}")
            print(f"   Response: {response.text}")
            return []
            
    except Exception as e:
        print(f"❌ Assignment listing failed: {e}")
        return []

def test_specific_assignment(course_id, assignment_id):
    """Test retrieval of a specific assignment with rubric"""
    print(f"\nTesting Specific Assignment: {assignment_id}")
    print("=" * 50)
    
    try:
        canvas_api = CanvasRubricAPI(*load_canvas_credentials())
        assignment_data = canvas_api.get_assignment_with_rubric(course_id, assignment_id)
        
        if assignment_data:
            print(f"✅ Assignment retrieved successfully!")
            print(f"   Name: {assignment_data.get('name', 'Unknown')}")
            print(f"   Points: {assignment_data.get('points_possible', 'Unknown')}")
            
            rubric = assignment_data.get('rubric', [])
            if rubric:
                print(f"   Rubric: {len(rubric)} criteria found")
                
                for i, criterion in enumerate(rubric, 1):
                    desc = criterion.get('description', 'No description')
                    points = criterion.get('points', 0)
                    ratings = len(criterion.get('ratings', []))
                    print(f"     {i}. {desc} ({points} pts, {ratings} rating levels)")
                
                # Test parsing
                parsed_criteria = canvas_api.parse_rubric_criteria(rubric)
                total_points = canvas_api.get_rubric_total_points(parsed_criteria)
                print(f"   Total Points: {total_points}")
                
            else:
                print("   ❌ No rubric found for this assignment")
                
            return assignment_data
        else:
            print("❌ Failed to retrieve assignment data")
            return None
            
    except Exception as e:
        print(f"❌ Assignment test failed: {e}")
        return None

def test_raw_api_call(course_id, assignment_id):
    """Test raw API call to see exact response"""
    print(f"\nRaw API Call Test: Course {course_id}, Assignment {assignment_id}")
    print("=" * 50)
    
    try:
        canvas_url, api_token = load_canvas_credentials()
        headers = {'Authorization': f'Bearer {api_token}'}
        
        url = f"{canvas_url}/api/v1/courses/{course_id}/assignments/{assignment_id}"
        params = {'include[]': ['rubric', 'rubric_assessment']}
        
        print(f"URL: {url}")
        print(f"Params: {params}")
        
        response = requests.get(url, headers=headers, params=params)
        
        print(f"Status Code: {response.status_code}")
        print(f"Response Headers: {dict(response.headers)}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"\nResponse Keys: {list(data.keys())}")
            
            if 'rubric' in data:
                rubric = data['rubric']
                print(f"Rubric Type: {type(rubric)}")
                print(f"Rubric Length: {len(rubric) if rubric else 'None'}")
                
                if rubric:
                    print("Rubric Sample (first criterion):")
                    print(json.dumps(rubric[0], indent=2))
            else:
                print("❌ No 'rubric' key in response")
                
            return data
        else:
            print(f"❌ Request failed")
            print(f"Response Text: {response.text}")
            return None
            
    except Exception as e:
        print(f"❌ Raw API test failed: {e}")
        return None

def main():
    """Main test function"""
    print("Canvas API Test Script")
    print("=" * 60)
    
    # Test basic connection
    if not test_canvas_connection():
        print("❌ Basic connection failed. Check your credentials.")
        return
    
    # Get test parameters
    print("\nEnter test parameters:")
    course_id = input("Course ID: ").strip()
    
    if not course_id:
        print("❌ Course ID is required")
        return
    
    # Test course access
    if not test_course_access(course_id):
        print("❌ Cannot access course. Check course ID and permissions.")
        return
    
    # List assignments
    assignments = test_course_assignments(course_id)
    
    if not assignments:
        print("❌ No assignments found")
        return
    
    # Test specific assignment
    assignment_id = input("Assignment ID to test (or press Enter to skip): ").strip()
    
    if assignment_id:
        print(f"\nTesting assignment {assignment_id}...")
        test_specific_assignment(course_id, assignment_id)
        test_raw_api_call(course_id, assignment_id)
    
    print("\n" + "=" * 60)
    print("Test complete!")

if __name__ == "__main__":
    main()
