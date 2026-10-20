import cv2
import easyocr
from ultralytics import YOLO

# 1. Initialize models and OCR reader
vehicle_model = YOLO("yolov8n.pt")
plate_model = YOLO("license_plate.pt")
reader = easyocr.Reader(['en'])

# 2. Open input video and get properties
video_path = "test_video.mp4"
cap = cv2.VideoCapture(video_path)

width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
fps = cap.get(cv2.CAP_PROP_FPS)
total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

# 3. Setup video writer for the output
fourcc = cv2.VideoWriter_fourcc(*'mp4v')
out = cv2.VideoWriter('output_video.mp4', fourcc, fps, (width, height))

print(f"Processing video: {total_frames} frames total...")

frame_count = 0
while cap.isOpened():
    ret, frame = cap.read()
    if not ret:
        break
    
    frame_count += 1
    if frame_count % 30 == 0:
        print(f"Processing frame {frame_count}/{total_frames}...")

    # COCO Class IDs for vehicles: 2=Car, 3=Motorcycle, 5=Bus, 7=Truck
    vehicle_classes = [2, 3, 5, 7]
    vehicle_results = vehicle_model(frame, classes=vehicle_classes, verbose=False)

    for v_result in vehicle_results:
        for v_box in v_result.boxes:
            vx1, vy1, vx2, vy2 = map(int, v_box.xyxy[0].tolist())
            
            # Draw green vehicle box
            cv2.rectangle(frame, (vx1, vy1), (vx2, vy2), (0, 255, 0), 2)
            
            # Crop vehicle region
            vehicle_crop = frame[vy1:vy2, vx1:vx2]
            if vehicle_crop.size == 0:
                continue
            
            # Detect license plates inside the vehicle
            plate_results = plate_model(vehicle_crop, conf=0.25, verbose=False)
            
            for p_result in plate_results:
                for p_box in p_result.boxes:
                    px1, py1, px2, py2 = map(int, p_box.xyxy[0].tolist())
                    
                    # Map back to full frame coordinates
                    x1, y1 = vx1 + px1, vy1 + py1
                    x2, y2 = vx1 + px2, vy1 + py2
                    
                    plate_crop = frame[y1:y2, x1:x2]
                    if plate_crop.size == 0:
                        continue
                        
                    ocr_result = reader.readtext(plate_crop)
                    
                    if ocr_result:
                        text = ocr_result[0][1]
                        cv2.putText(frame, f"PLATE: {text}", (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
                    
                    # Draw red plate box
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 255), 2)

    # Write processed frame to output video
    out.write(frame)

# Clean up resources
cap.release()
out.release()
print("Video processing complete! Saved as output_video.mp4")
