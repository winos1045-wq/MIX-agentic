import os
from google.genai import types



def write_file(working_directory , file_path , content) :
    abs_working_dir = os.path.abspath(working_directory)
    abs_file_path = os.path.abspath(os.path.join(working_directory , file_path) )
    
    # 1. فحص الأمان (Path Traversal Check) - سليم
    if not abs_file_path.startswith(abs_working_dir):
        return f'Error : {file_path} Access denied'
    
    # 2. تحديد الدليل الأب الذي يجب إنشاؤه
    file_parent_dir = os.path.dirname(abs_file_path) 
    
    # 3. محاولة إنشاء الأدلة المؤدية للملف إذا لم تكن موجودة
    try : 
        # os.makedirs(..., exist_ok=True) هو الحل.
        # ينشئ الدليل (والأدلة الأبوية) بسلام دون إطلاق خطأ إذا كان موجودًا بالفعل.
        os.makedirs(file_parent_dir, exist_ok=True) 
    except Exception as e:
        # إذا حدث أي خطأ آخر غير "موجود بالفعل" (مثل خطأ في الأذونات)
        return f"failed to create necessary directories: {file_parent_dir} = {e}"
        
    # 4. كتابة الملف (باستخدام المسار المطلق الآن)
    try : 
        # يجب استخدام abs_file_path لضمان الكتابة للمسار الذي تم التحقق منه وتجهيزه
        with open(abs_file_path,"w") as f: 
            f.write(content)
        return f"Successfuly wrote to '{file_path}' ({len(content)} characters written)"
    except Exception as e :
        # تم تغيير اسم المتغير الخطأ من 'path_file' إلى 'abs_file_path'
        return f'failed to write to file : {abs_file_path} , {e}'


schema_write_file = types.FunctionDeclaration(
    name="write_file",
    description="overwrites an existing file or write a new file if it doesn't exist (and creates parent dirs safly ), constrained to the working directory.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "file_path": types.Schema(
                type=types.Type.STRING,
                description="the path to the file to write.",
            ),
            "content": types.Schema(
                type=types.Type.STRING,
                description="the contents to write to the file as a string .",
            ),
        },
    ),
)