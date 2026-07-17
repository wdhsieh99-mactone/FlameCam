import os
import tkinter as tk 
from tkinter import messagebox
from datetime import datetime

class RaspberryPiGUI:
    def __init__(self, root):
        self.root = root
        self.root.geometry("1200x600")
        self.root.title("Reactflowlab_raspberrypi_GUI")

        self.fuel = ""
        self.ms=tk.StringVar()
        self.monitor_shutter=""
        self.set_number=0  
        self.row_number=0
        self.t = []
        self.shutter = []
        self.concentration=""
        self.pc=tk.StringVar()
        self.eq=""
        self.eqivalent=tk.StringVar()
        self.file_name = []
        self.file_choice=tk.StringVar()
        self.pc_choice=tk.StringVar()
        self.eq_choice=tk.StringVar()
        self.ms_choice=tk.StringVar()
        self.filename=tk.StringVar()
        self.choice = []
        self.parameter_list=[]
        self.Entry_widgets=[]
        self.timelapse_framerate = []
        self.radio_buttons = []
        self.img = tk.PhotoImage(file = 'ReFlowLab_signature.gif')
        self.default_file_name = datetime.now().strftime("%Y%m%d")
        self.create_widgets()

    def read_parameter(self, filename): 
        
        self.check_continue_ornot=True

        if filename=="": 
            messagebox.showinfo("File Not Found",f"Please enter the file name.")
            self.check_continue_ornot=False
            self.show_file_option()
            return

        file_path=os.path.abspath(filename+".txt")
        if not os.path.exists(file_path):
            result = messagebox.askokcancel("File Not Found",f"{filename}.txt doesn't exist.\nDo you want to create this file?" )
            if result:  # 如果用户选择“OK”
                with open(file_path, 'w') as file:
                     file.write(f"{filename}\n")  
                     self.check_continue_ornot=True
            else:
                self.check_continue_ornot=False
                return

        self.set_number=-1              
        self.choice.clear()               
        self.t.clear()           
        self.shutter.clear()             
        self.timelapse_framerate.clear()  
        self.file_name.clear()
        self.Entry_widgets.clear()
        
        with open(filename + '.txt', 'r') as file: 
             lines = file.readlines()

        for i, line in enumerate(lines):  
             row = line.strip().split()    
             if i == 0:
                    self.fuel = row[0]        
             else:                         
                   if row[0]=="Photos":       
                        self.choice.append(1)            
                   else:
                        self.choice.append(0) 
                   self.t.append(int(row[1])) 
                   self.shutter.append(int(row[2])) 
                   self.timelapse_framerate.append(int(row[3])) 
                   self.file_name.append("")
             self.set_number+=1 

    def create_widgets(self):
        self.Frame_title = tk.Frame(self.root) 
        self.Frame_title.pack()   
        self.Frame1 = tk.Frame(self.root) 
        self.Frame1.pack()                

        self.label_title1 = tk.Label( self.Frame_title , text="ReactingFlow  Lab",fg='#DC143C',font=('Georgia',18,'bold'),pady=3) 
        self.label_title1.grid(row=0, column=2) 
                                               
        self.label_img = tk.Label( self.Frame_title , image = self.img) 
        self.label_img.grid(row=0, column=3)

        '''self.label_filename = tk.Label(self.Frame1, text="txt file",fg='#666666',font=('Georgia',11,'bold'),pady=2) 
        self.label_filename.grid(row=0, column=0,padx=15)'''
        self.entry_filename = tk.Entry(self.Frame1,font=('Arial',11),width=10,justify='center',textvariable=self.filename)
        self.entry_filename.grid(row=1, column=0,padx=15)                                                 

        '''self.label_concentration = tk.Label(self.Frame1, text="pc",fg='#666666',font=('Georgia',11,'bold'),pady=2) 
        self.label_concentration.grid(row=0, column=1)'''
        self.entry_concentration = tk.Entry(self.Frame1,font=('Arial',11),width=8 ,justify='center',textvariable=self.pc)
        self.entry_concentration.grid(row=1, column=1)

        '''self.label_EQ = tk.Label(self.Frame1, text="eq",fg='#666666',font=('Georgia',11,'bold'),pady=2)
        self.label_EQ.grid(row=0, column=2,padx=15)'''
        self.entry_EQ = tk.Entry(self.Frame1,font=('Arial',11),width=8,justify='center',textvariable=self.eqivalent)
        self.entry_EQ.grid(row=1, column=2,padx=15)

        '''self.label_monitor_shutter = tk.Label(self.Frame1, text="monitor shutter",fg='#666666',font=('Georgia',11,'bold'),pady=2)
        self.label_monitor_shutter.grid(row=0, column=3)'''
        self.entry_monitor_shutter = tk.Entry(self.Frame1,font=('Arial',11),width=15,justify='center',textvariable=self.ms)
        self.entry_monitor_shutter.grid(row=1, column=3)

        self.label_tempmonitor_name = tk.Label(self.Frame1, text="monitor name",fg='#666666',bg="#D3D3D3",font=('Georgia',11,'bold'),pady=2)
        self.label_tempmonitor_name.grid(row=0, column=4,padx=15)
        self.entry_tempmonitor_name = tk.Entry(self.Frame1,font=('Arial',11),width=15,justify='center')
        self.entry_tempmonitor_name.grid(row=1, column=4,padx=15)

        self.button_next1 = tk.Button(self.Frame1, text="Next", bg="#D3D3D3",font=('Georgia',11),width=8,command=lambda:self.parameter_form(True)) 
        self.button_next1.grid(row=1, column=7)    

        self.button_monitor = tk.Button(self.Frame1, text="Monitor", bg="#D3D3D3",font=('Georgia',11),width=8,command=self.temp_monitor) 
        self.button_monitor.grid(row=1, column=6, padx=15) 

        self.button_select_file = tk.Button(self.Frame1, text="file.txt", bg="#D3D3D3",font=('Georgia',11),width=8,command= self.show_file_option) 
        self.button_select_file.grid(row=0, column=0,padx=15)  
        
        self.button_select_pc = tk.Button(self.Frame1, text="pc", bg="#D3D3D3",font=('Georgia',11),width=6,command= self.show_pc_option) 
        self.button_select_pc.grid(row=0, column=1)

        self.button_select_eq = tk.Button(self.Frame1, text="eq", bg="#D3D3D3",font=('Georgia',11),width=6,command= self.show_eq_option) 
        self.button_select_eq.grid(row=0, column=2,padx=15)

        self.button_select_ms = tk.Button(self.Frame1, text="monitor shutter", bg="#D3D3D3",font=('Georgia',11),width=13,command= self.show_ms_option) 
        self.button_select_ms.grid(row=0, column=3)

        self.frame_parameter_form = tk.Frame(self.root)    
        self.frame_parameter_form.pack()

        self.frame_button = tk.Frame(self.root) 
        self.frame_button.pack()   

        self.frame_message = tk.Frame(self.root)          
        self.frame_message.pack()
    
    def show_file_option(self):
        selection=[]
        option=[]
        for item in os.listdir(os.getcwd()): 
            if  item[-3:]=="txt":
                selection.append(item[0:-4])
        def select():
            selected_file= self.file_choice.get()
            self.filename.set(selected_file)
            sub_window.destroy() 

        sub_window = tk.Toplevel(self.root)
        sub_window.geometry("250x350")
        sub_window.title("Select file")
        self.label_select = tk.Label(sub_window, text="Select a file from the folder :",fg='#666666',font=('Georgia'),pady=10)
        self.label_select.pack()
        for i in range(0,len(selection)):
            words=selection[i]
            tem =tk.Radiobutton(sub_window,text=words,value=words,variable= self.file_choice) 
            tem.pack(ipadx=90,anchor='w')
            option.append(tem)
        option[0].select()
       
        self.button_select = tk.Button(sub_window, text="Select", bg="#D3D3D3",font=('Georgia',11),width=8,command=select)
        self.button_select.pack(padx=70,pady=10)

    def show_pc_option(self):
        pc=[0 ,10,20,30,40,50,60,70,80,85,90,95,100]
        option=[]
        def select_pc():
            selected_pc= self.pc_choice.get()
            self.pc.set(selected_pc)
            sub_window.destroy()  

        sub_window = tk.Toplevel(self.root)
        sub_window.geometry("250x450")
        sub_window.title("Select concentration")
        self.label_select = tk.Label(sub_window, text="Select concentration :",fg='#666666',font=('Georgia'),pady=10)
        self.label_select.pack()
        for i in range(0,len(pc)):
            tem =tk.Radiobutton(sub_window,text=pc[i],value=pc[i],variable= self.pc_choice) 
            tem.pack(ipadx=90,anchor='w')
            option.append(tem)
        option[0].select()
            
        self.button_select = tk.Button(sub_window, text="Select", bg="#D3D3D3",font=('Georgia',11),width=8,command=select_pc)
        self.button_select.pack(padx=70,pady=10)

    def show_eq_option(self):
        eq=[0,0.65,0.75,0.85,0.95]
        option=[]
        def select_eq():
            selected_eq= self.eq_choice.get()
            self.eqivalent.set(selected_eq)
            sub_window.destroy()  

        sub_window = tk.Toplevel(self.root)
        sub_window.geometry("250x350")
        sub_window.title("Select equivalent")
        self.label_select = tk.Label(sub_window, text="Select eqivalent :",fg='#666666',font=('Georgia'),pady=10)
        self.label_select.pack()
        for i in range(0,len(eq)):
            tem =tk.Radiobutton(sub_window,text=eq[i],value=eq[i],variable= self.eq_choice) 
            tem.pack(ipadx=90,anchor='w')
            option.append(tem)
        option[0].select()
            
        self.button_select = tk.Button(sub_window, text="Select", bg="#D3D3D3",font=('Georgia',11),width=8,command=select_eq)
        self.button_select.pack(padx=70,pady=10)

    def show_ms_option(self):
        ms=[500,1000,2000,4000,10000,25000,32000]
        option=[]
        def select_ms():
            selected_ms= self.ms_choice.get()
            self.ms.set(selected_ms)
            sub_window.destroy()  

        sub_window = tk.Toplevel(self.root)
        sub_window.geometry("250x350")
        sub_window.title("Select monitor shutter")
        self.label_select = tk.Label(sub_window, text="Select monitor shutter :",fg='#666666',font=('Georgia'),pady=10)
        self.label_select.pack()
        for i in range(0,len(ms)):
            tem =tk.Radiobutton(sub_window,text=ms[i],value=ms[i],variable= self.ms_choice) 
            tem.pack(ipadx=90,anchor='w')
            option.append(tem)
        option[0].select()
            
        self.button_select = tk.Button(sub_window, text="Select", bg="#D3D3D3",font=('Georgia',11),width=8,command=select_ms)
        self.button_select.pack(padx=70,pady=10)

    def parameter_form(self,read_or_not):                              

        if read_or_not:
            self.read_parameter(self.entry_filename.get()) 
        else:
            self.choice.clear()               
            self.t.clear()           
            self.shutter.clear()             
            self.timelapse_framerate.clear()  
            self.file_name.clear()
            self.Entry_widgets.clear()
        
            for i in range(self.set_number):
                self.t.append(self.parameter_list[0][i])
                self.shutter.append(self.parameter_list[1][i])
                self.timelapse_framerate.append(self.parameter_list[2][i])
                self.file_name.append(self.parameter_list[3][i])
                self.choice.append(self.parameter_list[4][i])
    
        self.clear_frame(self.frame_message)             
        self.clear_frame(self.frame_parameter_form)       
        self.clear_frame(self.frame_button)   

        if self.check_continue_ornot==False:
            return
        N = self.set_number                             
        self.concentration=self.entry_concentration.get()  

        self.radio_buttons.clear()

        if N!=0:
            self.Label_choice1=tk.Label(self.frame_parameter_form, text=f"photo",font=('Georgia',11,'bold'),fg='#666666',padx=7,pady=7).grid(row=0, column=1)
            self.Label_choice0=tk.Label(self.frame_parameter_form, text=f"video",font=('Georgia',11,'bold'),fg='#666666',pady=7).grid(row=0, column=2)
        for i in range(N):                                
                     
            self.choice[i] = tk.IntVar(value=self.choice[i])

            tk.Label(self.frame_parameter_form, text=f"{i+1}",font=('Georgia',8,'bold'),width=2,height=1,fg='black',pady=7).grid(row=i+1, column=0)

            rb1 = tk.Radiobutton(self.frame_parameter_form, text=" ",font=('Georgia',8), value=1, variable=self.choice[i])
            rb1.grid(row=1+i, column=1)
            self.radio_buttons.append(rb1)
            
            rb2 = tk.Radiobutton(self.frame_parameter_form, text=" ",font=('Georgia',8),padx=7,value=0, variable=self.choice[i])
            rb2.grid(row=1+i, column=2)
            self.radio_buttons.append(rb2)

            self.create_param_entry("t", self.t,i, 3,self.t[i])    
            self.create_param_entry("shutter", self.shutter, i, 4,self.shutter[i]) 
            self.create_param_entry("timelapse/framerate", self.timelapse_framerate, i, 5,self.timelapse_framerate[i])
            self.create_param_entry("file name", self.file_name, i, 6,self.file_name[i])
            self.row_number=i+1

        self.change_row = tk.Entry(self.frame_button, textvariable="",font=('Arial',11),width=13,justify='center') 
        self.change_row.grid(row=0, column=0,pady=10)
        
        self.button_add= tk.Button(self.frame_button, text="Add", bg="#D3D3D3", font=('Georgia',11),width=10, command=lambda: self.Add_remove_data(True) )
        self.button_add.grid(row=0,column=1,padx=30,pady=10)

        self.button_remove= tk.Button(self.frame_button, text="Remove", bg="#D3D3D3", font=('Georgia',11),width=10, command=lambda: self.Add_remove_data(False) )
        self.button_remove.grid(row=0,column=2,pady=10)
        if N!=0:
            self.button_next2 = tk.Button(self.frame_button, text="Next", bg="#D3D3D3", font=('Georgia',11),width=15,command= self.decide_file_name)
            self.button_next2.grid(row=0,column=4,pady=10,padx=30) 
        else:
            self.label_blank2 = tk.Label(self.frame_button, text="  " ,width=20,pady=10)
            self.label_blank2.grid(row=0, column=4,padx=30,pady=10)

        self.button_save = tk.Button(self.frame_button, text="Save", bg="#D3D3D3", font=('Georgia',11),width=10, command=self.save_parameters_to_file)
        self.button_save.grid(row=0,column=3,padx=30,pady=10)

    def decide_file_name(self): 
        self.clear_frame(self.frame_message) 
        N = self.set_number                                             
        self.concentration=self.entry_concentration.get() 
        self.eq=self.entry_EQ.get()
        self.monitor_shutter=self.entry_monitor_shutter.get()

        if self.concentration=="": 
            messagebox.showinfo("Data not found",f"Please enter the concentration.")
            self.check_continue_ornot=True
            self.get_entry_widgets_value()
            self.parameter_form(False)
            return
        if self.eq=="": 
            messagebox.showinfo("Data not found",f"Please enter the equivalent .")
            self.check_continue_ornot=True
            self.get_entry_widgets_value()
            self.parameter_form(False)
            return
        if self.monitor_shutter=="": 
            messagebox.showinfo("Data not found",f"Please enter the monitor shutter.")
            self.check_continue_ornot=True
            self.get_entry_widgets_value()
            self.parameter_form(False)
            return

        for i in range(N):                                 
            photos_folder = os.path.expanduser(f"~/Desktop/{self.default_file_name}/{self.fuel}/25ms/{self.concentration}pc/{self.eq}eq") 
            videos_folder = os.path.expanduser(f"~/Desktop/{self.default_file_name}/{self.fuel}/record/{self.concentration}pc/{self.eq}eq")
            os.makedirs(photos_folder, exist_ok=True)      
            os.makedirs(videos_folder, exist_ok=True)
        
        photos_shutter=[]
        videos_shutter=[]
        for i in range(N):                    
            if self.choice[i].get() == 1:  
                shutter=self.shutter[i].get()
                photos_shutter.append(shutter)
                N=photos_shutter.count(shutter)-1
                n=self.get_subfolder_number( photos_folder,shutter)
                subfolder_name = f"{self.fuel}/25ms/{self.concentration}pc/{self.eq}eq/{shutter}_{n+1+N}"  
             
            else:   
                shutter=self.shutter[i].get()
                videos_shutter.append(shutter)
                N=videos_shutter.count(shutter)-1
                n=self.get_subfolder_number( videos_folder,shutter)
                subfolder_name = f"{self.fuel}/record/{self.concentration}pc/{self.eq}eq/{shutter}_{n+1+N}"
                
            self.file_name[i].set(self.default_file_name + '/' + subfolder_name) 

        self.button_next2.grid_forget()      
        self.button_next3 = tk.Button(self.frame_button, text="Create folder",bg="#D3D3D3", width=15,font=('Georgia',11), command=self.create_folder) 
        self.button_next3.grid( row=0,column=4,padx=30,pady=10)   

        for rb in self.radio_buttons:       
            rb.config(state=tk.DISABLED)
        for i,entry in enumerate(self.Entry_widgets):
            if (i-3)%4 !=0: 
                entry.config(state=tk.DISABLED)

    def create_param_entry(self, text, param_list, index, col, default_value): 
        tk.Label(self.frame_parameter_form, text=f"{text}",font=('Georgia',11,'bold'),fg='#666666',pady=10).grid(row=0, column=col) 
         
        param_list[index] =tk.StringVar(value=default_value)

        if text=='file name':
            entry = tk.Entry(self.frame_parameter_form, textvariable=param_list[index],font=('Arial',11),width=32,justify='center') 
        elif text=='t':
            entry = tk.Entry(self.frame_parameter_form, textvariable=param_list[index],font=('Arial',11),width=13,justify='center') 
        elif text=='shutter':
            entry = tk.Entry(self.frame_parameter_form, textvariable=param_list[index],font=('Arial',11),width=13,justify='center') 
        else:
            entry = tk.Entry(self.frame_parameter_form, textvariable=param_list[index],font=('Arial',11),width=23,justify='center') 
        entry.grid(row=1+index, column=col,pady=0) 
        self.Entry_widgets.append(entry)

    def create_folder(self):
        
        N = self.set_number
        existence_confirmation = 0           
        for i in range(N):                    
            file_name = self.file_name[i].get()  
            output_folder = os.path.expanduser(f"~/Desktop/{file_name}") 

            if os.path.exists(output_folder):   
                existence_confirmation = 1       
                command = "File " + file_name + " is already exists."
                
                messagebox.showinfo("error",f"{command}\n")
                         
                self.clear_frame(self.frame_message)                        
                error_msg = tk.StringVar()       
                label_error_msg = tk.Label(self.frame_message, fg="red", font=('Arial',10), textvariable=error_msg)
                label_error_msg.grid(row=N+2, column=3)
                error_msg.set(command)
                break                           

        if existence_confirmation == 0: 
            for i in range(N):          
                file_name = self.file_name[i].get()
                output_folder = os.path.expanduser(f"~/Desktop/{file_name}") 
                os.makedirs(output_folder)    
                print(f"Folder created at {output_folder}")  

            for i,entry in enumerate(self.Entry_widgets):
                if (i-3)%4 ==0: 
                    entry.config(state=tk.DISABLED)
            self.change_row.config(state=tk.DISABLED)
            self.button_add.grid_forget()
            self.button_remove.grid_forget()
            self.button_save.grid_forget()
            self.button_next3.grid_forget() 

            self.label_blank = tk.Label(self.frame_button, text="  " ,width=50,pady=10)
            self.label_blank.grid(row=0, column=1,padx=30,pady=10)
            self.button_start1 = tk.Button(self.frame_button, text="Start", bg="#D3D3D3", width=15, font=('Georgia',11),command=self.take_photo)
            self.button_start1.grid(row=0, column=2,padx=30,pady=10) 
            
    def take_photo(self): 
        self.button_start1.grid_forget() 
        self.clear_frame(self.frame_message)
        N = self.set_number
        for i in range(N):
            file_name = self.file_name[i].get()
            output_folder = os.path.expanduser(f"~/Desktop/{file_name}")

            msg_var = tk.StringVar()
            label_msg = tk.Label(self.frame_message, fg="black", font=('Arial',10), textvariable=msg_var)
            label_msg.grid(row=i, column=0)

            T = self.t[i].get() 
            Shutter = self.shutter[i].get()
            Timelapse_framerate = self.timelapse_framerate[i].get()

            if self.choice[i].get() == 1:
                command = f"libcamera-still -t {T} --rotation 180 --width 4608 --height 2592 --timelapse {Timelapse_framerate} -preview --gain 10 --shutter {Shutter} --lens-position 1.25 -r -q 100 --awbgains 2.0,2.3  -o {output_folder}/image%02d.jpg"
            else:
                command = f"libcamera-vid -t {T} --rotation 180 --width 1920 --height 1080 --lens-position 1.25 --framerate {Timelapse_framerate} --gain 10 --shutter {Shutter} --awbgains 2.0,2.3 -q 100 -o {output_folder}/record.h264 --save-pts {output_folder}/timestamps.csv"

            msg_var.set(command)            
            os.system(command)                
        self.end_monitor() 

        self.label_blank3 = tk.Label(self.frame_button, text="  " ,width=20,pady=10)
        self.label_blank3.grid(row=0, column=2,padx=30,pady=10)

    def end_monitor(self): 
        msg_var3 = tk.StringVar()
        label_msg3 = tk.Label(self.frame_message, fg="black", font=('Arial',10), textvariable=msg_var3)
        label_msg3.grid(column=0)

        self.monitor_shutter=self.entry_monitor_shutter.get()
        monitor_folder = os.path.expanduser(f"~/Desktop/{self.default_file_name}/{self.fuel}/exp/{self.concentration}pc/{self.eq}eq")
        os.makedirs(monitor_folder, exist_ok=True)
        max_monitor_folder_number = self. get_monitor_subfolder_number(monitor_folder)

        monitor_file_name = os.path.expanduser(f"~/Desktop/{self.default_file_name}/{self.fuel}/exp/{self.concentration}pc/{self.eq}eq/test{max_monitor_folder_number + 1}_{self.monitor_shutter}")
        os.makedirs(monitor_file_name, exist_ok=True)
        print(f"Folder created at {monitor_file_name}")
        command = f"libcamera-vid -t 0 --rotation 180 --width 1920 --height 1080 --framerate 30 --shutter {self.monitor_shutter} --gain 10 --awbgains 2.0,2.3 -q 100 -o {monitor_file_name}/record.h264 --save-pts {monitor_file_name}/timestamps.csv"
        os.system(command)
        msg_var3.set(command)

    def temp_monitor(self):
        self.clear_frame(self.frame_message)

        msg_var2 = tk.StringVar()
        label_msg2 = tk.Label(self.frame_message, fg="black", font=('Arial',10), textvariable=msg_var2)
        label_msg2.grid(column=0)

        self.monitor_shutter=self.entry_monitor_shutter.get()
        if  self.monitor_shutter=="": 
            messagebox.showinfo("Data not found",f"Please enter the monitor shutter.")
            if self.entry_filename.get()=="":
                self.check_continue_ornot=False
            else:
                self.check_continue_ornot=True
            self.get_entry_widgets_value()
            self.parameter_form(False)
            return

        monitor_folder = os.path.expanduser(f"~/Desktop/{self.default_file_name}/temp_monitor")
        os.makedirs(monitor_folder, exist_ok=True)
        max_monitor_folder_number = self.get_monitor_subfolder_number(monitor_folder)
        monitorfilename=self.entry_tempmonitor_name.get()

        monitor_file_name = os.path.expanduser(f"~/Desktop/{self.default_file_name}/temp_monitor/test{max_monitor_folder_number+1}_{self.monitor_shutter}_{monitorfilename}")
        os.makedirs(monitor_file_name, exist_ok=True)
        print(f"Folder created at {monitor_file_name}")
        command = f"libcamera-vid -t 0 --rotation 180 --width 1920 --height 1080 --framerate 30 --shutter {self.monitor_shutter} --gain 10 --awbgains 2.0,2.3 -q 100 -o {monitor_file_name}/record.h264 --save-pts {monitor_file_name}/timestamps.csv"
        os.system(command)
        msg_var2.set(command)

    def get_monitor_subfolder_number(self, folder): 
        max_number = 0                                
        for item in os.listdir(folder):   
            
            if  item.startswith("test"):
                try:
                    n=item.index("_")
                    number = int(item[4:n]) 
                    max_number = max(max_number, number)
                except ValueError:
                   pass
        return max_number 

    def get_subfolder_number(self, folder, shutter_value):
        unique_shutter = sorted(set([s.get() for s in self.shutter]))  # 获取唯一的shutter值并排序
        number_of_subfolder = {s: 0 for s in unique_shutter}  # 创建一个字典来存储每个shutter值的子文件夹数

        # 遍历文件夹内容并统计每个shutter值的子文件夹数量
        for item in os.listdir(folder):
            for s in unique_shutter:
                if item.startswith(str(s)+'_'):
                    number_of_subfolder[s] += 1
                    break

        return number_of_subfolder.get(shutter_value, 0)  # 返回指定shutter_value的子文件夹数

    def clear_frame(self, frame):
        for widget in frame.winfo_children():
            widget.destroy()
    
    def save_parameters_to_file(self):
     
        filename = self.entry_filename.get() + '.txt'
        with open(filename, 'w') as file:
            file.write(f"{self.fuel}\n")  # 写入第一个参数（例如燃料类型）

            for i in range(self.set_number):
                choice_str = "Photos" if self.choice[i].get() == 1 else "Videos"
                line = f"{choice_str} {self.t[i].get()} {self.shutter[i].get()} {self.timelapse_framerate[i].get()}\n"
                file.write(line)
        print("Parameters saved successfully.")
    
    def get_entry_widgets_value(self):
        self.parameter_list=[[],[],[],[],[],0]

        for i in range(self.set_number):
             self.parameter_list[0].append(self.Entry_widgets[0+4*i].get())
             self.parameter_list[1].append(self.Entry_widgets[1+4*i].get())
             self.parameter_list[2].append(self.Entry_widgets[2+4*i].get())
             self.parameter_list[3].append(self.Entry_widgets[3+4*i].get())
             self.parameter_list[4].append(self.choice[i].get())
        self.parameter_list[5]=self.set_number

    def Add_remove_data(self,function_choice): 

        self.get_entry_widgets_value()

        r =( self.change_row.get()).strip().split()  #r是一堆欄數,ex:2 4 14，若沒輸入東西會是空列表
        for i,rn in enumerate( r): #換成int type
            r[i]=int(rn)
        r.sort() #由小到大排
        if function_choice:
            for i,rn in enumerate(r):   #rn=2,rn=4,rn=14
                n=int(rn)+i
                self.parameter_list[0].insert(n,"11000") 
                self.parameter_list[1].insert(n,"32000")  
                self.parameter_list[2].insert(n,"1000") 
                self.parameter_list[3].insert(n,"") 
                self.parameter_list[4].insert(n,1) 
                self.parameter_list[5]=self.parameter_list[5]+1
        else:
            for i,rn in enumerate(r):   #rn=2,rn=4,rn=14
                n=int(rn)-i-1
                self.parameter_list[0].pop(n) 
                self.parameter_list[1].pop(n)  
                self.parameter_list[2].pop(n) 
                self.parameter_list[3].pop(n) 
                self.parameter_list[4].pop(n) 
                self.parameter_list[5]=self.parameter_list[5]-1
        self.set_number=self.parameter_list[5]
        self.parameter_form(False)

if __name__ == "__main__":
    root = tk.Tk()
    app = RaspberryPiGUI(root)
    root.mainloop()



