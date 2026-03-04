# check_db.py
import pymysql
import sys

# 直接从你的app.py中复制过来的配置
MYSQL_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': 'Yang123!',  # 请确保这是你的正确密码
    'database': 'reservoir_monitor',
    'charset': 'utf8mb4'
}

def check_names():
    try:
        print("正在连接数据库...")
        conn = pymysql.connect(**MYSQL_CONFIG)
        cursor = conn.cursor()
        
        print("\n1. 【reservoirs 表】中的水库名称 (地图标记来源):")
        cursor.execute("SELECT id, name, latitude IS NOT NULL as has_coord FROM reservoirs ORDER BY id")
        reservoirs = cursor.fetchall()
        for rid, name, has_coord in reservoirs:
            coord_status = "✅ 有坐标" if has_coord else "❌ 无坐标"
            print(f"   ID {rid}: {name} ({coord_status})")
        
        print("\n2. 【hydrology_data 表】中的水库名称 (水文数据来源):")
        cursor.execute("SELECT DISTINCT reservoir_name, COUNT(*) as data_count FROM hydrology_data GROUP BY reservoir_name ORDER BY reservoir_name")
        hydrology = cursor.fetchall()
        if hydrology:
            for h_name, count in hydrology:
                print(f"   {h_name} (对应 {count} 条水文数据)")
        else:
            print("   (表为空或无数据)")
        
        print("\n3. 【关键检查】可能的不匹配项:")
        cursor.execute("""
            SELECT DISTINCT h.reservoir_name 
            FROM hydrology_data h 
            WHERE NOT EXISTS (
                SELECT 1 FROM reservoirs r WHERE r.name = h.reservoir_name
            )
        """)
        mismatches = cursor.fetchall()
        if mismatches:
            for (mismatch_name,) in mismatches:
                print(f"   ❗ 水文数据中的 '{mismatch_name}' 在水库信息表中找不到对应项！")
        else:
            print("   ✅ 两张表的水库名称基本可以对应上。")
        
        conn.close()
        print("\n检查完成。请将以上输出结果完整复制。")
        
    except pymysql.err.OperationalError as e:
        print(f"数据库连接失败: {e}")
        print("请检查: 1. MySQL服务是否启动 2. 配置中的密码是否正确")
    except Exception as e:
        print(f"发生错误: {e}")

if __name__ == '__main__':
    check_names()