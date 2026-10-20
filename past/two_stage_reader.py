import cv2
import easyocr
from ultralytics import YOLO

# 1. Load the general vehicle model and the specific plate model
vehicle_model = YOLO("yolov8n.pt") 
plate_model = YOLO("license_plate.pt")
reader = easyocr.Reader(['en'])

image = cv2.imread("test_Image.jpeg")

# COCO Class IDs: 2=Car, 3=Motorcycle, 5=Bus, 7=Truck
vehicle_classes = [2, 3, 5, 7] 

# 2. Detect vehicles
vehicle_results = vehicle_model(image, classes=vehicle_classes)

for v_result in vehicle_results:
    for v_box in v_result.boxes:
        # Get vehicle coordinates
        vx1, vy1, vx2, vy2 = map(int, v_box.xyxy[0].tolist())
        
        # Draw green box for the vehicle
        cv2.rectangle(image, (vx1, vy1), (vx2, vy2), (0, 255, 0), 2)
        
        # Crop the vehicle from the main image
        vehicle_crop = image[vy1:vy2, vx1:vx2]
        
        # 3. Detect plates ONLY inside the cropped vehicle
        plate_results = plate_model(vehicle_crop, conf=0.25)
        
        for p_result in plate_results:
            for p_box in p_result.boxes:
                # Get plate coordinates relative to the cropped vehicle
                px1, py1, px2, py2 = map(int, p_box.xyxy[0].tolist())
                
                # Convert crop coordinates back to full image coordinates
                x1, y1 = vx1 + px1, vy1 + py1
                x2, y2 = vx1 + px2, vy1 + py2
                
                # Crop the exact plate for text extraction
                plate_crop = image[y1:y2, x1:x2]
                ocr_result = reader.readtext(plate_crop)
                
                # 4. Draw red boxes and text for the plates
                if ocr_result:
                    text = ocr_result[0][1]
                    print(f"Found Plate: {text}")
                    cv2.putText(image, f"PLATE: {text}", (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
                    cv2.rectangle(image, (x1, y1), (x2, y2), (0, 0, 255), 2)

cv2.imwrite("final_output.jpeg", image)
print("Processing complete. Saved as final_output.jpeg")
